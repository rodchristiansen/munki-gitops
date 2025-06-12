// sqs-listener.js  –  sample “all-AWS” cache refresher for Munki
//
// Edit the CONFIG block only.  Everything else is generic.
//

import fs   from 'fs';
import path from 'path';
import os   from 'os';
import util from 'util';
import { exec } from 'child_process';
import {
  SQSClient,
  ReceiveMessageCommand,
  DeleteMessageCommand,
} from '@aws-sdk/client-sqs';

const execAsync = util.promisify(exec);

// ──────────────────
// CONFIG  (sample values)
// ──────────────────
const CONFIG = {
  region      : 'us-east-1',

  // SQS queue that receives “commit pushed” messages
  queueUrl    : 'https://sqs.us-east-1.amazonaws.com/123456789012/munki-commits',

  // Munki repo to pull
  repoUrl     : 'https://github.com/example-org/Munki.git',

  // Local working copy and object storage
  workingCopy : '/Users/Shared/Munki',
  bucketUrl   : 's3://munki-prod-bucket/repo',

  // Paths to CLI tools (change if you use Homebrew-on-Intel, etc.)
  awsCli      : '/opt/homebrew/bin/aws',
  logDir      : path.join(os.homedir(), 'Library/Logs/MunkiListener'),
};

// ──────────────────
function ts() { return new Date().toISOString().split('.')[0].replace('T', ' '); }

fs.mkdirSync(CONFIG.logDir, { recursive: true });
const log = fs.createWriteStream(path.join(CONFIG.logDir, 'listener.log'), { flags: 'a' });
const err = fs.createWriteStream(path.join(CONFIG.logDir, 'listener_error.log'), { flags: 'a' });

console.log  = m => log.write(`[${ts()}] ${m}\n`);
console.error = m => err.write(`[${ts()}] ${m}\n`);

async function run(cmd, opts = {}) {
  const { stdout, stderr } = await execAsync(cmd, { ...opts, maxBuffer: 1024 ** 2 * 5 });
  if (stdout) console.log(stdout.trim());
  if (stderr) console.error(stderr.trim());
}

async function syncFromS3(sub) {
  const src = `${CONFIG.bucketUrl}/deployment/${sub}`;
  const dst = `${CONFIG.workingCopy}/deployment/${sub}`;
  await run(`${CONFIG.awsCli} s3 sync ${src} ${dst} --delete --exclude ".DS_Store" --exclude "**/.DS_Store"`);
}

async function ensureRepo() {
  if (!fs.existsSync(path.join(CONFIG.workingCopy, '.git'))) {
    console.log('Cloning repo…');
    await run(`git clone ${CONFIG.repoUrl} ${CONFIG.workingCopy}`);
  }
}

async function refreshRepo() {
  const o = { cwd: CONFIG.workingCopy };
  await run('git reset --hard', o);
  await run('git clean -fd',    o);
  await run('git fetch --all',  o);
  await run('git pull --rebase',o);
}

async function pollQueue() {
  const sqs = new SQSClient({ region: CONFIG.region });
  let backOff = 0;

  while (true) {
    const resp = await sqs.send(new ReceiveMessageCommand({
      QueueUrl: CONFIG.queueUrl,
      WaitTimeSeconds: 20,
      MaxNumberOfMessages: 1,
    }));

    if (!resp.Messages?.length) {
      backOff = Math.min(backOff + 5, 60);
      await new Promise(r => setTimeout(r, backOff * 1000));
      continue;
    }
    backOff = 0;

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
      console.log('Cache refresh complete ✓');
    } catch (e) {
      console.error(`Processing error: ${e.message}`);
      /* message re-appears after visibility timeout */
    }
  }
}

// ─── bootstrap ───
(async () => {
  await ensureRepo();
  console.log(`Polling ${CONFIG.queueUrl}`);
  await pollQueue();
})().catch(e => console.error(`Fatal: ${e.message}`));