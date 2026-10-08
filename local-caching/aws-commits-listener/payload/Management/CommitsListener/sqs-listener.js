// sqs-listener.js — "all-AWS" cache refresher for a Munki caching server.
//
// • Every value comes from the environment (the LaunchDaemon's
//   EnvironmentVariables block). Never hard-code a queue URL, bucket or
//   credential in this file. AWS credentials come from the standard chain:
//   an instance profile, IAM Roles Anywhere, or AWS_PROFILE.
// • Uses "aws s3 sync" for fast, resumable transfers.
// ----------------------------------------------------------------------

import fs   from 'fs';
import path from 'path';
import util from 'util';
import { exec } from 'child_process';
import {
  SQSClient,
  ReceiveMessageCommand,
  DeleteMessageCommand,
} from '@aws-sdk/client-sqs';

const execAsync = util.promisify(exec);

// ────────────────
// CONFIG — driven entirely by environment variables. Examples:
//   MUNKI_AWS_REGION  = us-east-1
//   MUNKI_SQS_URL     = https://sqs.<region>.amazonaws.com/<account-id>/munki-commits
//   MUNKI_BUCKET_URL  = s3://<your-bucket>[/<prefix>]
// ────────────────
const CONFIG = {
  region      : process.env.MUNKI_AWS_REGION || 'us-east-1',
  queueUrl    : process.env.MUNKI_SQS_URL,

  // Optional: a command that prints a short-lived bearer token for the git
  // remote. When unset, git uses whatever credential helper the machine has.
  gitTokenCmd : process.env.MUNKI_GIT_TOKEN_COMMAND || '',
  repoUrl     : process.env.MUNKI_REPO_URL,
  workingCopy : process.env.MUNKI_WORKING_COPY || '/Users/Shared/munki-repo',

  // The bucket (and optional prefix) that holds deployment/{pkgs,icons,...}:
  // the same layout the git hooks and the push pipeline write.
  bucketUrl   : process.env.MUNKI_BUCKET_URL,

  awsCli      : process.env.MUNKI_AWS_CLI || '/opt/homebrew/bin/aws',
  logDir      : process.env.MUNKI_LOG_DIR || path.join(process.env.HOME || '/tmp', 'Library/Logs/CommitsListener'),
};

for (const k of ['queueUrl', 'repoUrl', 'bucketUrl']) {
  if (!CONFIG[k]) { console.error(`Missing required env for CONFIG.${k}`); process.exit(2); }
}

// ────────────────
function ts() { return new Date().toISOString().split('.')[0].replace('T', ' '); }

fs.mkdirSync(CONFIG.logDir, { recursive: true });
const log = fs.createWriteStream(path.join(CONFIG.logDir, 'listener.log'),       { flags: 'a' });
const err = fs.createWriteStream(path.join(CONFIG.logDir, 'listener_error.log'), { flags: 'a' });

// Keep credentials out of the logs: bearer tokens and presigned-URL
// signatures, wherever they turn up.
const redact = t => String(t)
  .replace(/Bearer [^"\s]+/g, 'Bearer ***')
  .replace(/(X-Amz-(?:Signature|Security-Token|Credential)=)[^&"\s]+/gi, '$1***');

// Every log line goes through redact, so no call site can forget it.
console.log   = m => log.write(`[${ts()}] ${redact(m)}\n`);
console.error = m => err.write(`[${ts()}] ${redact(m)}\n`);

async function run(cmd, opts = {}) {
  try {
    const { stdout, stderr } = await execAsync(cmd, { ...opts, maxBuffer: 1024 ** 2 * 5 });
    if (stdout) console.log(stdout.trim());
    if (stderr) console.error(stderr.trim());
  } catch (e) {
    throw new Error(redact(e.message));
  }
}

// The mirror copies what is in the bucket, so --delete is right here: a
// package retired from the repo should leave the cache too.
async function syncFromS3(sub) {
  const src = `${CONFIG.bucketUrl}/deployment/${sub}`;
  const dst = path.join(CONFIG.workingCopy, 'deployment', sub);
  fs.mkdirSync(dst, { recursive: true });
  await run(`"${CONFIG.awsCli}" s3 sync "${src}" "${dst}" --delete --exclude ".DS_Store" --exclude "*/.DS_Store" --region "${CONFIG.region}" --only-show-errors`);
}

// ────────────────
// Git auth. Short-lived tokens expire while the listener sits idle between
// commits, so refresh ahead of expiry and once more on an auth failure, rather
// than failing every refresh until the service restarts.
// ────────────────
const TOKEN_TTL_MS = 40 * 60 * 1000;
let gitToken = '';
let gitTokenAt = 0;

async function refreshGitToken() {
  if (!CONFIG.gitTokenCmd) return;
  const { stdout } = await execAsync(CONFIG.gitTokenCmd, { maxBuffer: 1024 ** 2 });
  const t = stdout.trim();
  if (!t) throw new Error('git token command printed nothing');
  gitToken = t;
  gitTokenAt = Date.now();
  console.log('Refreshed git access token');
}

function gitEnv() {
  // The header goes in through git's environment config (GIT_CONFIG_COUNT and
  // friends), never on the command line, where any local user could read it
  // from the process list, and never into a git config file on disk.
  const env = { ...process.env, GIT_TERMINAL_PROMPT: '0' };
  if (gitToken) {
    env.GIT_CONFIG_COUNT = '1';
    env.GIT_CONFIG_KEY_0 = 'http.extraHeader';
    env.GIT_CONFIG_VALUE_0 = `Authorization: Bearer ${gitToken}`;
  }
  return env;
}

function isAuthFailure(e) {
  return /Authentication failed|could not read Username|could not read Password|HTTP 401|HTTP 403/i.test(e.message || '');
}

async function git(args, opts = {}) {
  if (CONFIG.gitTokenCmd && Date.now() - gitTokenAt >= TOKEN_TTL_MS) await refreshGitToken();
  try {
    await run(`git ${args}`, { ...opts, env: gitEnv() });
  } catch (e) {
    if (!CONFIG.gitTokenCmd || !isAuthFailure(e)) throw e;
    console.log('Git authentication failed; refreshing the token and retrying once');
    await refreshGitToken();
    await run(`git ${args}`, { ...opts, env: gitEnv() });
  }
}

async function ensureRepo() {
  if (!fs.existsSync(path.join(CONFIG.workingCopy, '.git'))) {
    console.log('Cloning repo…');
    await git(`clone "${CONFIG.repoUrl}" "${CONFIG.workingCopy}"`);
  }
}

// The mirror is never edited by hand, so reset to the remote rather than
// rebasing onto it: a rebase that stops on a conflict leaves the mirror
// half-applied and serving a mix of two commits.
async function refreshRepo() {
  const o = { cwd: CONFIG.workingCopy };
  await git('fetch --prune origin', o);
  await git('reset --hard origin/HEAD', o);
  await git('clean -fd -e deployment/pkgs -e deployment/icons -e deployment/catalogs', o);
}

async function pollQueue() {
  const sqs = new SQSClient({ region: CONFIG.region });

  while (true) {
    // Long polling: the receive itself waits up to 20s for a message, so an
    // empty queue costs one request per 20s and a commit is picked up at once.
    let resp;
    try {
      resp = await sqs.send(new ReceiveMessageCommand({
        QueueUrl: CONFIG.queueUrl,
        WaitTimeSeconds: 20,
        MaxNumberOfMessages: 1,
      }));
    } catch (e) {
      console.error(`SQS receive error: ${e.message}`);
      await new Promise(r => setTimeout(r, 30 * 1000));
      continue;
    }

    if (!resp.Messages?.length) continue;

    const m = resp.Messages[0];
    console.log('Commit event received – refreshing cache');

    try {
      await refreshRepo();
      await syncFromS3('pkgs');
      await syncFromS3('icons');
      await syncFromS3('catalogs');

      await sqs.send(new DeleteMessageCommand({
        QueueUrl: CONFIG.queueUrl,
        ReceiptHandle: m.ReceiptHandle,
      }));
      console.log('Cache refresh complete');
    } catch (e) {
      console.error(`Processing error: ${e.message}`);
      /* message re-appears after the visibility timeout */
    }
  }
}

// ─── bootstrap ───
(async () => {
  await ensureRepo();
  console.log(`Polling ${CONFIG.queueUrl}`);
  await pollQueue();
})().catch(e => { console.error(`Fatal: ${e.message}`); process.exitCode = 1; });
