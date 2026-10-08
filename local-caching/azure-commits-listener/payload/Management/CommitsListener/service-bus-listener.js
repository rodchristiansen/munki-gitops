// service-bus-listener.js
//
// Munki caching server: consumes Azure Service Bus messages and refreshes a
// local Munki working copy from git and Azure Blob Storage.
//
// • Every cloud-specific value comes from the environment (the LaunchDaemon's
//   EnvironmentVariables block). Never hard-code a connection string or SAS in
//   this file.
// • Uses "azcopy sync" for fast, resumable transfers.
// • Prefers a managed identity (an Azure VM or an Arc-enabled Mac) for both
//   Service Bus and storage, so no secret exists at all. Give the identity
//   Azure Service Bus Data Receiver on the subscription and Storage Blob Data
//   Reader on the container.
// ----------------------------------------------------------------------

import fs   from 'fs';
import path from 'path';
import util from 'util';
import { exec } from 'child_process';
import { ServiceBusClient } from '@azure/service-bus';
import { DefaultAzureCredential } from '@azure/identity';

const execAsync = util.promisify(exec);

// ────────────────
// CONFIG — driven entirely by environment variables. Examples:
//   MUNKI_SB_NAMESPACE  = <namespace>.servicebus.windows.net   (managed identity)
//   MUNKI_BLOB_URL      = https://<storage-account>.blob.core.windows.net/<container>
//   MUNKI_MSI_CLIENT_ID = <client id of a user-assigned identity, if not system-assigned>
//
// Fallbacks for a machine with no managed identity. Both are secrets, and the
// SAS has to go on azcopy's command line, where local administrators can see
// it in the process list; keep it read-only, scoped to the container and
// short-lived.
//   MUNKI_SB_CONNECTION = Endpoint=sb://<namespace>.servicebus.windows.net/;...
//   MUNKI_BLOB_SAS      = ?sv=...&sp=rl&sig=...
// ────────────────
const CONFIG = {
  sbNamespace  : process.env.MUNKI_SB_NAMESPACE || '',
  sbConnection : process.env.MUNKI_SB_CONNECTION || '',
  msiClientId  : process.env.MUNKI_MSI_CLIENT_ID || '',
  sbTopic      : process.env.MUNKI_SB_TOPIC || 'munki-commits',
  sbSub        : process.env.MUNKI_SB_SUB   || 'cache-server-1',

  // Optional: a command that prints a short-lived bearer token for the git
  // remote. When unset, git uses whatever credential helper the machine has.
  gitTokenCmd  : process.env.MUNKI_GIT_TOKEN_COMMAND || '',
  repoUrl      : process.env.MUNKI_REPO_URL,
  workingCopy  : process.env.MUNKI_WORKING_COPY || '/Users/Shared/munki-repo',

  // The container that holds deployment/{pkgs,icons,catalogs,...}: the same
  // layout the git hooks and the push pipeline write.
  blobUrl      : process.env.MUNKI_BLOB_URL,
  sas          : process.env.MUNKI_BLOB_SAS || '',

  azcopy       : process.env.MUNKI_AZCOPY || '/opt/homebrew/bin/azcopy',
  logDir       : process.env.MUNKI_LOG_DIR || path.join(process.env.HOME || '/tmp', 'Library/Logs/CommitsListener'),
};

for (const k of ['repoUrl', 'blobUrl']) {
  if (!CONFIG[k]) { console.error(`Missing required env for CONFIG.${k}`); process.exit(2); }
}
if (!CONFIG.sbNamespace && !CONFIG.sbConnection) {
  console.error('Set MUNKI_SB_NAMESPACE (managed identity) or MUNKI_SB_CONNECTION'); process.exit(2);
}

// ────────────────
function ts() { return new Date().toISOString().split('.')[0].replace('T', ' '); }

fs.mkdirSync(CONFIG.logDir, { recursive: true });
const log = fs.createWriteStream(path.join(CONFIG.logDir, 'listener.log'),       { flags: 'a' });
const err = fs.createWriteStream(path.join(CONFIG.logDir, 'listener_error.log'), { flags: 'a' });

// Keep credentials out of the logs: bearer tokens, SAS signatures and
// connection-string keys, wherever they turn up (command output, exec error
// messages that echo the command line, SDK errors).
const redact = t => String(t)
  .replace(/Bearer [^"\s]+/g, 'Bearer ***')
  .replace(/([?&]sig=)[^&"\s]+/gi, '$1***')
  .replace(/(SharedAccessKey=)[^;"\s]+/gi, '$1***');

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

// With no SAS, azcopy signs in with the managed identity by itself.
function azcopyEnv() {
  const env = { ...process.env };
  if (!CONFIG.sas) {
    env.AZCOPY_AUTO_LOGIN_TYPE = 'MSI';
    if (CONFIG.msiClientId) env.AZCOPY_MSI_CLIENT_ID = CONFIG.msiClientId;
  }
  return env;
}

// The mirror copies what is in blob, so --delete-destination is right here:
// a package retired from the repo should leave the cache too.
async function syncFromBlob(sub) {
  const src = `${CONFIG.blobUrl}/deployment/${sub}${CONFIG.sas}`;
  const dst = path.join(CONFIG.workingCopy, 'deployment', sub);
  fs.mkdirSync(dst, { recursive: true });
  await run(`"${CONFIG.azcopy}" sync "${src}" "${dst}" --recursive --delete-destination=true --exclude-pattern="*.DS_Store"`, { env: azcopyEnv() });
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

async function main() {
  await ensureRepo();

  const sb = CONFIG.sbNamespace
    ? new ServiceBusClient(CONFIG.sbNamespace, new DefaultAzureCredential(
        CONFIG.msiClientId ? { managedIdentityClientId: CONFIG.msiClientId } : {}))
    : new ServiceBusClient(CONFIG.sbConnection);
  const rx = sb.createReceiver(CONFIG.sbTopic, CONFIG.sbSub);

  rx.subscribe({
    processMessage: async msg => {
      console.log('Commit event received – refreshing cache');
      try {
        await refreshRepo();
        await syncFromBlob('pkgs');
        await syncFromBlob('icons');
        await syncFromBlob('catalogs');
        await rx.completeMessage(msg);
        console.log('Cache refresh complete');
      } catch (e) {
        console.error(`Process error: ${e.message}`);
        await rx.abandonMessage(msg);
      }
    },
    processError: async e => console.error(`Service Bus error: ${e.error?.message || e.message}`),
  });

  console.log(`Listening on topic ${CONFIG.sbTopic} / subscription ${CONFIG.sbSub}`);
}

main().catch(e => { console.error(`Fatal: ${e.message}`); process.exitCode = 1; });
