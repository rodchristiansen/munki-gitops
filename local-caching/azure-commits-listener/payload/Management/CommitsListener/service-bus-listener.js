// bus-listener-azure.js
//
// Generic listener that consumes Azure Service Bus messages and refreshes
// a local Munki working copy from Azure Blob Storage.
//
// • Everything cloud-specific lives in the CONFIG block.
// • Uses “azcopy sync” for fast, resumable transfers.
// ----------------------------------------------------------------------

import fs   from 'fs';
import path from 'path';
import os   from 'os';
import util from 'util';
import { exec }  from 'child_process';
import { ServiceBusClient } from '@azure/service-bus';

const execAsync = util.promisify(exec);

// ────────────────
// CONFIG (edit me)
// ────────────────
const CONFIG = {
  // Service Bus
  sbConnection : 'Endpoint=sb://<namespace>.servicebus.windows.net/;SharedAccessKeyName=<Name>;SharedAccessKey=<Key>',
  sbTopic      : 'munki-commits',
  sbSub        : 'cache-server-1',

  // Git + working copy
  repoUrl      : 'https://azure-devops.example.com/<org>/<project>/_git/Munki',
  workingCopy  : '/Users/Shared/Munki',

  // Azure Blob (container must already exist)
  blobUrl      : 'https://<storage>.blob.core.windows.net/munki',
  sas          : '?sv=2023-11-03&ss=b&srt=co&sp=rl&se=2026-01-01T00:00:00Z&sig=<sig>',

  // Tools and logs
  azcopy       : '/opt/homebrew/bin/azcopy',
  logDir       : path.join(os.homedir(), 'Library/Logs/MunkiListener'),
};

// ────────────────
function ts() { return new Date().toISOString().split('.')[0].replace('T', ' '); }

fs.mkdirSync(CONFIG.logDir, { recursive: true });
const log = fs.createWriteStream(path.join(CONFIG.logDir, 'listener.log'),       { flags: 'a' });
const err = fs.createWriteStream(path.join(CONFIG.logDir, 'listener_error.log'), { flags: 'a' });

console.log  = m => log.write(`[${ts()}] ${m}\n`);
console.error = m => err.write(`[${ts()}] ${m}\n`);

async function run(cmd, opts = {}) {
  const { stdout, stderr } = await execAsync(cmd, { ...opts, maxBuffer: 1024 ** 2 * 5 });
  if (stdout) console.log(stdout.trim());
  if (stderr) console.error(stderr.trim());
}

async function syncFromBlob(sub) {
  const src = `${CONFIG.blobUrl}/repo/deployment/${sub}${CONFIG.sas}`;
  const dst = `${CONFIG.workingCopy}/deployment/${sub}`;
  await run(`${CONFIG.azcopy} sync "${src}" "${dst}" --recursive --delete-destination=true`);
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

async function main() {
  await ensureRepo();

  const sb  = new ServiceBusClient(CONFIG.sbConnection);
  const rx  = sb.createReceiver(CONFIG.sbTopic, CONFIG.sbSub);

  rx.subscribe({
    processMessage: async msg => {
      console.log('Commit event received – refreshing cache');
      try {
        await refreshRepo();
        await syncFromBlob('pkgs');
        await syncFromBlob('icons');
        await syncFromBlob('catalogs');
        await rx.completeMessage(msg);
        console.log('Cache refresh complete ✓');
      } catch (e) {
        console.error(`Process error: ${e.message}`);
        await rx.abandonMessage(msg);
      }
    },
    processError: e => console.error(`Service Bus error: ${e.message}`),
  });

  console.log(`Listening on topic ${CONFIG.sbTopic} / subscription ${CONFIG.sbSub}`);
}

main().catch(e => console.error(`Fatal: ${e.message}`));