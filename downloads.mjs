import { validateHostname, validateRepository, safeRelativePath, githubURL } from './model.mjs';

export const DOWNLOAD_PLATFORMS = {
  windows: { label: 'Windows', starter: 'Start-Taskboard.cmd', note: 'Extract the ZIP, then double-click the .cmd file. Windows may show an unknown publisher or SmartScreen prompt. Verify the source and follow your organization\'s requirements.' },
  macos: { label: 'macOS', starter: 'Start-Taskboard.command', note: 'Extract the ZIP, then double-click the .command file. macOS may ask you to confirm a downloaded or unsigned script. Verify the source using your organization\'s approved process.' },
  linux: { label: 'Linux', starter: 'Start-Taskboard.sh', note: 'Extract the ZIP, then double-click the .sh file. Some file managers open it as text. In file properties, allow it to run as a program, then choose Run in Terminal.' },
};
const encoder = new TextEncoder();
const decoder = new TextDecoder();
const SHA256 = /^[0-9a-f]{64}$/;
const MAX_RUNTIME = 25 * 1024 * 1024;
const MAX_TEMPLATE = 256 * 1024;

function boardLocator(snapshot) {
  if (!snapshot || snapshot.demo !== false || snapshot.schema_version !== 1) throw new Error('Downloads are unavailable in a demo or unconfirmed workspace. Return to the live board.');
  return { hostname: validateHostname(snapshot.hostname), repository: validateRepository(snapshot.repository) };
}

export function buildDownloadRequest(snapshot, { task, action = 'run' } = {}, runtimeHash) {
  const board = boardLocator(snapshot);
  if (!SHA256.test(runtimeHash)) throw new Error('Invalid runtime SHA-256. Refresh and try again.');
  if (!['run', 'install-publisher'].includes(action)) throw new Error('Invalid download action.');
  const request = { schema_version: 1, ...board, runtime_sha256: runtimeHash, action };
  if (action === 'install-publisher') return request;
  const current = snapshot.tasks?.find(item => item.number === task?.number);
  if (!current || !Number.isSafeInteger(current.number) || current.number < 1 || current.status !== 'open'
    || task.status !== 'open' || !Number.isSafeInteger(current.spec?.revision) || current.spec.revision < 1
    || !SHA256.test(current.digest) || task.digest !== current.digest || task.spec?.revision !== current.spec.revision) {
    throw new Error('The task has changed or is no longer open. Close the details and refresh the board before trying again.');
  }
  return { ...request, issue: current.number, revision: current.spec.revision, task_digest: current.digest };
}

function downloadsRoot(pageURL) {
  const page = new URL(pageURL);
  if (!['https:', 'http:'].includes(page.protocol) || page.username || page.password) throw new Error('Invalid download URL. Open it from the deployed board.');
  return new URL('./downloads/', page);
}

export function publisherInstructions(snapshot, pageURL) {
  boardLocator(snapshot);
  const root = downloadsRoot(pageURL);
  return `Help me set up the Agent Relay publishing skill, rules, and session hooks in the current project.\n\nTask board repository: ${githubURL(snapshot)}\nDownload assets: ${root.href}\n\nFirst read README.en.md and integrations/taskboard-publish/SKILL.md in that repository. Use the board's publisher setup package to configure the integration, preserving existing project settings. Record only the callback information for the current project and Agent session. Do not upload private conversations or secrets.\n\nWhen you identify work that can be delegated independently, prepare a self-contained task as if the receiving agent has no access to our conversation. Include the complete context: goal, background, pinned code commit, required resources, allowed changes, acceptance commands, and deliverables. Prepare the handoff with non-goals, constraints, assumptions, environment, and stop conditions. In handoff.review, give concrete first_step, inputs, and completion answers. Resolve any blocking_questions before publication; do not invent missing information.\n\nBefore asking for approval, reread the final execution_prompt and its declared resources from the receiving agent's perspective. Explain where to start, where each necessary input lives, and how to establish completion. The schema checks structure and declared blockers; you must review whether the task is complete without our conversation. Show me this concrete proposal and ask whether to publish it. Publish only after my explicit approval. Keep the same task and proposal when retrying. When a result arrives, review it in the original session and ask me to confirm acceptance. Do not publish automatically just because a task is complex or a hook fires.`;
}

const crcTable = Uint32Array.from({ length: 256 }, (_, index) => {
  let crc = index;
  for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  return crc >>> 0;
});
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = (crc >>> 8) ^ crcTable[(crc ^ byte) & 0xff];
  return (crc ^ 0xffffffff) >>> 0;
}

// All entries are regular files. Unix attributes preserve .command/.sh execution bits.
export function storedZip(entries) {
  if (!Array.isArray(entries) || !entries.length || entries.length > 65535) throw new Error('Invalid number of ZIP files.');
  const seen = new Set();
  const files = entries.map(entry => {
    safeRelativePath(entry.name);
    if (seen.has(entry.name.toLowerCase())) throw new Error('Duplicate ZIP filename.');
    seen.add(entry.name.toLowerCase());
    const name = encoder.encode(entry.name);
    const data = typeof entry.data === 'string' ? encoder.encode(entry.data) : entry.data;
    if (name.length > 65535 || !(data instanceof Uint8Array)) throw new Error('Invalid ZIP file contents.');
    return { name, data, crc: crc32(data), executable: Boolean(entry.executable) };
  });
  const localSize = files.reduce((size, file) => size + 30 + file.name.length + file.data.length, 0);
  const centralSize = files.reduce((size, file) => size + 46 + file.name.length, 0);
  const size = localSize + centralSize + 22;
  if (size > MAX_RUNTIME + 2 * MAX_TEMPLATE + 64 * 1024) throw new Error('ZIP download package is too large.');
  const zip = new Uint8Array(size);
  const view = new DataView(zip.buffer);
  let local = 0;
  let central = localSize;
  for (const file of files) {
    view.setUint32(local, 0x04034b50, true);
    view.setUint16(local + 4, 20, true);
    view.setUint16(local + 6, 0x0800, true);
    view.setUint16(local + 12, 33, true);
    view.setUint32(local + 14, file.crc, true);
    view.setUint32(local + 18, file.data.length, true);
    view.setUint32(local + 22, file.data.length, true);
    view.setUint16(local + 26, file.name.length, true);
    zip.set(file.name, local + 30);
    zip.set(file.data, local + 30 + file.name.length);
    view.setUint32(central, 0x02014b50, true);
    view.setUint16(central + 4, 0x0314, true);
    view.setUint16(central + 6, 20, true);
    view.setUint16(central + 8, 0x0800, true);
    view.setUint16(central + 14, 33, true);
    view.setUint32(central + 16, file.crc, true);
    view.setUint32(central + 20, file.data.length, true);
    view.setUint32(central + 24, file.data.length, true);
    view.setUint16(central + 28, file.name.length, true);
    view.setUint32(central + 38, ((file.executable ? 0o100755 : 0o100644) << 16) >>> 0, true);
    view.setUint32(central + 42, local, true);
    zip.set(file.name, central + 46);
    local += 30 + file.name.length + file.data.length;
    central += 46 + file.name.length;
  }
  view.setUint32(central, 0x06054b50, true);
  view.setUint16(central + 8, files.length, true);
  view.setUint16(central + 10, files.length, true);
  view.setUint32(central + 12, centralSize, true);
  view.setUint32(central + 16, localSize, true);
  return zip;
}

async function fetchBytes(url, limit, fetchImpl) {
  const response = await fetchImpl(url, { credentials: 'omit', redirect: 'error', mode: 'same-origin', cache: 'no-cache', signal: AbortSignal.timeout(30000) });
  if (!response.ok) throw new Error(`Download assets are unavailable (HTTP ${response.status}). Try again later or ask the board maintainer to publish the download assets.`);
  if (Number(response.headers.get('content-length')) > limit) throw new Error('Download asset is too large. Contact the board maintainer.');
  const reader = response.body?.getReader();
  if (!reader) throw new Error('Download asset is empty. Try again later.');
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.length;
      if (length > limit) {
        await reader.cancel();
        throw new Error('Download asset is too large. Contact the board maintainer.');
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  if (!length) throw new Error('Download asset is empty. Try again later.');
  const data = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { data.set(chunk, offset); offset += chunk.length; }
  return data;
}

export async function createDownloadPackage({ snapshot, task, platform, action = 'run' }, { pageURL = globalThis.location?.href, fetchImpl = globalThis.fetch, cryptoImpl = globalThis.crypto } = {}) {
  if (!Object.hasOwn(DOWNLOAD_PLATFORMS, platform)) throw new Error('Choose Windows, macOS, or Linux.');
  const selected = DOWNLOAD_PLATFORMS[platform];
  const pending = buildDownloadRequest(snapshot, { task, action }, '0'.repeat(64));
  const root = downloadsRoot(pageURL);
  if (!cryptoImpl?.subtle?.digest) throw new Error('This browser cannot verify download integrity. Use a modern browser with HTTPS support.');
  let manifest;
  try { manifest = JSON.parse(decoder.decode(await fetchBytes(new URL('runtime.json', root), 16 * 1024, fetchImpl))); }
  catch (error) {
    if (error instanceof SyntaxError) throw new Error('Invalid download manifest. Contact the board maintainer.');
    throw error;
  }
  if (manifest?.schema_version !== 1 || manifest.file !== 'taskboard-runtime.zip' || !SHA256.test(manifest.sha256)
    || !Number.isSafeInteger(manifest.size) || manifest.size < 1 || manifest.size > MAX_RUNTIME) {
    throw new Error('Download manifest is invalid or too large. Contact the board maintainer.');
  }
  const [runtime, bootstrap, starter] = await Promise.all([
    fetchBytes(new URL(manifest.file, root), manifest.size, fetchImpl),
    fetchBytes(new URL('bootstrap.py', root), MAX_TEMPLATE, fetchImpl),
    fetchBytes(new URL(selected.starter, root), MAX_TEMPLATE, fetchImpl),
  ]);
  if (runtime.length !== manifest.size) throw new Error('Runtime size does not match the manifest. Refresh and try again.');
  const digest = [...new Uint8Array(await cryptoImpl.subtle.digest('SHA-256', runtime))].map(byte => byte.toString(16).padStart(2, '0')).join('');
  if (digest !== manifest.sha256) throw new Error('Runtime SHA-256 verification failed. Refresh and try again.');
  const request = { ...pending, runtime_sha256: digest };
  const title = action === 'run' ? `Task #${request.issue}` : 'Publisher setup';
  const readme = `Agent Relay · ${title}\n\n1. Extract the entire ZIP and keep all files in the same folder.\n2. Double-click ${selected.starter}. Do not run it inside the ZIP preview.\n3. On first use, follow the installation and login guide in the terminal. If a tool is missing, open its official installation instructions, install it, and retry.\n4. Choose Codex or Claude Code${action === 'run' ? ', then confirm the task to begin. The tool waits for GitHub confirmation and uploads the result automatically.' : ', then follow the guide to connect your project. Your existing Agent prepares a proposal and publishes it only after your explicit approval.'}\n\n${selected.note}\nThis is a script launcher package, not a signed native app. Do not bypass system or organizational security protections. Python, Git, GitHub CLI, and your chosen Agent are required. The guide checks for them and offers installation help; it does not install tools silently.\n\nTask repository: https://${request.hostname}/${request.repository}\n${action === 'run' ? `Issue: ${request.issue}; revision: ${request.revision}\nGitHub holds the live task status. Downloading this package does not claim the task.\n` : ''}Runtime SHA-256: ${digest}\n`;
  const bytes = storedZip([
    { name: 'runtime.zip', data: runtime }, { name: 'bootstrap.py', data: bootstrap },
    { name: 'request.json', data: `${JSON.stringify(request, null, 2)}\n` },
    { name: selected.starter, data: starter, executable: platform !== 'windows' },
    { name: 'READ-ME.txt', data: readme },
  ]);
  return { bytes, request, filename: action === 'run' ? `taskboard-task-${request.issue}-${platform}.zip` : `taskboard-publisher-${platform}.zip` };
}
