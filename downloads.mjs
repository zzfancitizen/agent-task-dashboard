import { validateHostname, validateRepository, safeRelativePath, githubURL } from './model.mjs';

export const DOWNLOAD_PLATFORMS = {
  windows: { label: 'Windows', starter: 'Start-Taskboard.cmd', note: '解压后双击 .cmd。Windows 可能显示发布者未知或 SmartScreen 提示；请先查看文件来源与组织要求。' },
  macos: { label: 'macOS', starter: 'Start-Taskboard.command', note: '解压后双击 .command。macOS 可能因下载来源或未签名脚本要求确认；请按组织认可的方式核验来源。' },
  linux: { label: 'Linux', starter: 'Start-Taskboard.sh', note: '解压后双击 .sh。部分文件管理器会打开文本；请在文件属性中允许作为程序运行，再选择在终端运行。' },
};
const encoder = new TextEncoder();
const decoder = new TextDecoder();
const SHA256 = /^[0-9a-f]{64}$/;
const MAX_RUNTIME = 25 * 1024 * 1024;
const MAX_TEMPLATE = 256 * 1024;

function boardLocator(snapshot) {
  if (!snapshot || snapshot.demo !== false || snapshot.schema_version !== 1) throw new Error('演示或未确认的工作空间不能下载运行包，请返回真实看板。');
  return { hostname: validateHostname(snapshot.hostname), repository: validateRepository(snapshot.repository) };
}

export function buildDownloadRequest(snapshot, { task, action = 'run' } = {}, runtimeHash) {
  const board = boardLocator(snapshot);
  if (!SHA256.test(runtimeHash)) throw new Error('运行时 SHA-256 无效，请刷新后重试。');
  if (!['run', 'install-publisher'].includes(action)) throw new Error('下载操作无效。');
  const request = { schema_version: 1, ...board, runtime_sha256: runtimeHash, action };
  if (action === 'install-publisher') return request;
  const current = snapshot.tasks?.find(item => item.number === task?.number);
  if (!current || !Number.isSafeInteger(current.number) || current.number < 1 || current.status !== 'open'
    || task.status !== 'open' || !Number.isSafeInteger(current.spec?.revision) || current.spec.revision < 1
    || !SHA256.test(current.digest) || task.digest !== current.digest || task.spec?.revision !== current.spec.revision) {
    throw new Error('任务已变化或当前不可领取，请关闭详情并刷新看板后重试。');
  }
  return { ...request, issue: current.number, revision: current.spec.revision, task_digest: current.digest };
}

function downloadsRoot(pageURL) {
  const page = new URL(pageURL);
  if (!['https:', 'http:'].includes(page.protocol) || page.username || page.password) throw new Error('下载地址无效，请从已部署的看板打开。');
  return new URL('./downloads/', page);
}

export function publisherInstructions(snapshot, pageURL) {
  boardLocator(snapshot);
  const root = downloadsRoot(pageURL);
  return `请帮我在当前项目中接入 Agent Relay 的任务发布 skill、规则和会话 hook。\n\n任务看板仓库：${githubURL(snapshot)}\n下载资源：${root.href}\n\n先读取该仓库的 README 和 integrations/taskboard-publish/SKILL.md，使用看板的发布接入安装包完成配置，保留项目已有设置。只记录当前项目与当前 Agent 会话的回调信息，不上传私人会话或密钥。\n\n发现可独立委派的工作时，请准备完整上下文：目标、背景、固定代码提交、必要资料、允许修改范围、验收命令和交付要求。先给我一份具体的发布提案，询问我是否发布；只有在我明确同意后才发布。重试时保留同一份任务与提案。收到交付后，由原会话检查结果，再由我确认验收。不要仅因为任务复杂或 hook 触发就自动发布。`;
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
  if (!Array.isArray(entries) || !entries.length || entries.length > 65535) throw new Error('ZIP 文件数量无效。');
  const seen = new Set();
  const files = entries.map(entry => {
    safeRelativePath(entry.name);
    if (seen.has(entry.name.toLowerCase())) throw new Error('ZIP 文件名重复。');
    seen.add(entry.name.toLowerCase());
    const name = encoder.encode(entry.name);
    const data = typeof entry.data === 'string' ? encoder.encode(entry.data) : entry.data;
    if (name.length > 65535 || !(data instanceof Uint8Array)) throw new Error('ZIP 文件内容无效。');
    return { name, data, crc: crc32(data), executable: Boolean(entry.executable) };
  });
  const localSize = files.reduce((size, file) => size + 30 + file.name.length + file.data.length, 0);
  const centralSize = files.reduce((size, file) => size + 46 + file.name.length, 0);
  const size = localSize + centralSize + 22;
  if (size > MAX_RUNTIME + 2 * MAX_TEMPLATE + 64 * 1024) throw new Error('ZIP 下载包过大。');
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
  if (!response.ok) throw new Error(`下载资源暂不可用（HTTP ${response.status}）。请稍后重试，或联系看板维护者发布下载资源。`);
  if (Number(response.headers.get('content-length')) > limit) throw new Error('下载资源过大，请联系看板维护者。');
  const reader = response.body?.getReader();
  if (!reader) throw new Error('下载资源为空，请稍后重试。');
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.length;
      if (length > limit) {
        await reader.cancel();
        throw new Error('下载资源过大，请联系看板维护者。');
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  if (!length) throw new Error('下载资源为空，请稍后重试。');
  const data = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { data.set(chunk, offset); offset += chunk.length; }
  return data;
}

export async function createDownloadPackage({ snapshot, task, platform, action = 'run' }, { pageURL = globalThis.location?.href, fetchImpl = globalThis.fetch, cryptoImpl = globalThis.crypto } = {}) {
  if (!Object.hasOwn(DOWNLOAD_PLATFORMS, platform)) throw new Error('请选择 Windows、macOS 或 Linux。');
  const selected = DOWNLOAD_PLATFORMS[platform];
  const pending = buildDownloadRequest(snapshot, { task, action }, '0'.repeat(64));
  const root = downloadsRoot(pageURL);
  if (!cryptoImpl?.subtle?.digest) throw new Error('浏览器无法验证下载完整性，请使用支持 HTTPS 的现代浏览器。');
  let manifest;
  try { manifest = JSON.parse(decoder.decode(await fetchBytes(new URL('runtime.json', root), 16 * 1024, fetchImpl))); }
  catch (error) {
    if (error instanceof SyntaxError) throw new Error('下载资源清单无效，请联系看板维护者。');
    throw error;
  }
  if (manifest?.schema_version !== 1 || manifest.file !== 'taskboard-runtime.zip' || !SHA256.test(manifest.sha256)
    || !Number.isSafeInteger(manifest.size) || manifest.size < 1 || manifest.size > MAX_RUNTIME) {
    throw new Error('下载资源清单无效或过大，请联系看板维护者。');
  }
  const [runtime, bootstrap, starter] = await Promise.all([
    fetchBytes(new URL(manifest.file, root), manifest.size, fetchImpl),
    fetchBytes(new URL('bootstrap.py', root), MAX_TEMPLATE, fetchImpl),
    fetchBytes(new URL(selected.starter, root), MAX_TEMPLATE, fetchImpl),
  ]);
  if (runtime.length !== manifest.size) throw new Error('运行时大小与清单不一致，请刷新后重试。');
  const digest = [...new Uint8Array(await cryptoImpl.subtle.digest('SHA-256', runtime))].map(byte => byte.toString(16).padStart(2, '0')).join('');
  if (digest !== manifest.sha256) throw new Error('运行时 SHA-256 校验失败，请刷新后重试。');
  const request = { ...pending, runtime_sha256: digest };
  const title = action === 'run' ? `任务 #${request.issue}` : '发布接入';
  const readme = `Agent Relay · ${title}\n\n1. 完整解压此 ZIP，保留所有文件在同一文件夹。\n2. 双击 ${selected.starter}。不要在 ZIP 预览中运行。\n3. 首次使用请跟随终端中的安装与登录引导。缺少工具时可打开官方安装说明，完成后重试。\n4. 选择 Codex 或 Claude Code${action === 'run' ? '，确认任务后开始执行；工具会等待 GitHub 确认，并自动回传交付。' : '，按引导接入你的项目。由现有 Agent 准备提案，只有你明确同意后才发布。'}\n\n${selected.note}\n这是脚本启动包，不是签名的原生应用；不要绕过系统或组织的安全保护。需要 Python、Git、GitHub CLI 和所选 Agent。引导会检查并提供安装帮助；不会静默安装。\n\n任务仓库：https://${request.hostname}/${request.repository}\n${action === 'run' ? `Issue：${request.issue}；修订：${request.revision}\n实时状态以 GitHub 为准，下载文件本身不代表已领取。\n` : ''}运行时 SHA-256：${digest}\n`;
  const bytes = storedZip([
    { name: 'runtime.zip', data: runtime }, { name: 'bootstrap.py', data: bootstrap },
    { name: 'request.json', data: `${JSON.stringify(request, null, 2)}\n` },
    { name: selected.starter, data: starter, executable: platform !== 'windows' },
    { name: 'READ-ME.txt', data: readme },
  ]);
  return { bytes, request, filename: action === 'run' ? `taskboard-task-${request.issue}-${platform}.zip` : `taskboard-publisher-${platform}.zip` };
}
