export const STATUS = {
  open: { label: '待领取', tone: 'open' },
  claimed: { label: '已领取', tone: 'active' },
  running: { label: '执行中', tone: 'active' },
  submitted: { label: '待验收', tone: 'review' },
  accepted: { label: '已完成', tone: 'done' },
  cancelled: { label: '已取消', tone: 'neutral' },
  failed: { label: '执行失败', tone: 'failed' },
};
export const CATEGORIES = { code: '代码', docs: '文档', research: '调研' };
const AGENTS = ['codex', 'claude'];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const COMMIT = /^[0-9a-f]{40}$/i;
const SHA256 = /^[0-9a-f]{64}$/i;
const forbiddenControl = /[\u0000-\u001f\u007f]/;

export function validateHostname(value) {
  if (typeof value !== 'string' || !value || value.length > 253 || !value.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label))) {
    throw new Error('GitHub hostname 无效，请使用域名，不包含协议、端口或路径。');
  }
  return value.toLowerCase();
}

export function validateRepository(value) {
  if (typeof value !== 'string' || value.length > 200 || !/^[a-z0-9][a-z0-9-]*\/[a-z0-9_.-]+$/i.test(value) || ['.', '..'].includes(value.split('/')[1])) {
    throw new Error('仓库格式应为 owner/repo。');
  }
  return value;
}

function issueNumber(value) {
  if (!Number.isSafeInteger(value) || value < 1) throw new Error('Issue 编号必须是正整数。');
  return value;
}

export function safeRelativePath(value, allowDirectory = false) {
  if (typeof value !== 'string' || !value || value.length > 1024 || forbiddenControl.test(value) || /[\\%?#:]/.test(value) || value.startsWith('/')) throw new Error('路径必须为不含 .. 的相对路径。');
  const path = allowDirectory && value.endsWith('/') ? value.slice(0, -1) : value;
  if (!path || path.split('/').some(part => !part || part === '.' || part === '..' || part.toLowerCase() === '.git')) throw new Error('路径必须为不含 .. 或 Git 元数据的相对路径。');
  return value;
}

export function githubURL(snapshot, path = '') {
  const host = validateHostname(snapshot.hostname);
  const repo = validateRepository(snapshot.repository);
  if (path) safeRelativePath(path);
  return `https://${host}/${repo}${path ? `/${path.split('/').map(encodeURIComponent).join('/')}` : ''}`;
}

export function taskRunURL(snapshot, number, agent) {
  if (agent !== undefined && !AGENTS.includes(agent)) throw new Error('请选择兼容的 Agent。');
  const params = new URLSearchParams({ repo: validateRepository(snapshot.repository), issue: String(issueNumber(number)), hostname: validateHostname(snapshot.hostname) });
  if (agent !== undefined) params.set('agent', agent);
  return `taskboard://run?${params}`;
}

export function taskCommand(snapshot, number, agent = 'codex') {
  if (!AGENTS.includes(agent)) throw new Error('请选择兼容的 Agent。');
  return `taskboard --repo ${validateRepository(snapshot.repository)} --hostname ${validateHostname(snapshot.hostname)} run ${issueNumber(number)} --agent ${agent}`;
}

export function safeHTTPS(value) {
  if (typeof value !== 'string' || forbiddenControl.test(value) || /\\/.test(value)) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.port || !url.hostname) return null;
    validateHostname(url.hostname);
    return url.href;
  } catch { return null; }
}

export function repositoryURL(value, hostname) {
  const safe = safeHTTPS(value);
  if (!safe) throw new Error('源仓库必须是有效的 HTTPS GitHub 仓库地址。');
  const url = new URL(safe);
  if (url.hostname !== validateHostname(hostname) || url.search || url.hash || url.pathname.endsWith('/') || url.pathname.endsWith('.git')) throw new Error('源仓库必须与当前 GitHub 域名一致，且使用 owner/repo 地址。');
  validateRepository(url.pathname.slice(1));
  return `${url.origin}${url.pathname}`;
}

export function resourceURL(resource, hostname) {
  try {
    if (!COMMIT.test(resource.commit)) return null;
    const repo = repositoryURL(resource.repository, hostname);
    const path = safeRelativePath(resource.path).split('/').map(encodeURIComponent).join('/');
    return `${repo}/blob/${resource.commit}/${path}`;
  } catch { return null; }
}

export function filterTasks(tasks, { query = '', status = 'all', agent = 'all', category = 'all' } = {}) {
  const term = query.trim().toLocaleLowerCase();
  return tasks.filter(task => {
    const spec = task.spec;
    const statusMatch = status === 'all' || (status === 'active' ? ['claimed', 'running'].includes(task.status) : task.status === status);
    const agentMatch = agent === 'all' || spec.execution?.compatible_agents?.includes(agent);
    const categoryMatch = category === 'all' || (spec.category || 'code') === category;
    const text = `${spec.title} ${spec.prompt} ${task.author || ''} #${task.number}`.toLocaleLowerCase();
    return statusMatch && agentMatch && categoryMatch && (!term || text.includes(term));
  });
}

export function taskCounts(tasks) {
  return tasks.reduce((counts, task) => {
    counts.total += 1;
    if (task.status === 'open') counts.open += 1;
    if (['claimed', 'running'].includes(task.status)) counts.active += 1;
    if (task.status === 'submitted') counts.submitted += 1;
    if (task.status === 'accepted') counts.accepted += 1;
    return counts;
  }, { total: 0, open: 0, active: 0, submitted: 0, accepted: 0 });
}

export function normalizeSnapshot(data) {
  if (!data || data.schema_version !== 1 || !Array.isArray(data.tasks) || typeof data.demo !== 'boolean' || typeof data.generated_at !== 'string') throw new Error('任务快照格式不兼容，需要 schema_version 1。');
  const hostname = validateHostname(data.hostname);
  if (data.repository !== '') validateRepository(data.repository);
  if (data.generated_at && !Number.isFinite(Date.parse(data.generated_at))) throw new Error('任务快照的同步时间无效。');
  const seen = new Set();
  for (const task of data.tasks) {
    if (!task || !Object.hasOwn(STATUS, task.status) || !task.spec || typeof task.spec.title !== 'string' || typeof task.spec.prompt !== 'string' || !Array.isArray(task.spec.execution?.compatible_agents) || !task.spec.execution.compatible_agents.length || task.spec.execution.compatible_agents.some(agent => !AGENTS.includes(agent))) throw new Error('任务快照包含无效任务，请重新生成。');
    issueNumber(task.number);
    if (seen.has(task.number)) throw new Error('任务快照包含重复的 Issue 编号。');
    seen.add(task.number);
  }
  if (data.tasks.length && !data.repository) throw new Error('任务快照缺少所属仓库。');
  return { ...data, hostname };
}

function required(value, label, max = 40000) {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`请填写${label}。`);
  if (value.trim().length > max) throw new Error(`${label}过长；任务文件必须小于 48 KB。`);
  if (value.includes('\0')) throw new Error(`${label}不能包含空字符。`);
  return value.trim();
}

function lines(value) {
  return String(value || '').split('\n').map(line => line.trim()).filter(Boolean);
}

function boundedNumber(value, min, max, label) {
  const number = Number(value);
  if (!Number.isInteger(number) || number < min || number > max) throw new Error(`${label}必须为 ${min}–${max} 的整数。`);
  return number;
}

export function buildTaskExport(values, { hostname = 'github.com', taskId } = {}) {
  if (!UUID.test(taskId)) throw new Error('任务 ID 无效，请重新打开发布表单。');
  const title = required(values.title, '任务标题', 200);
  const prompt = required(values.prompt, '任务说明');
  const reason = required(values.delegation_reason, '委派原因', 4000);
  const repository = repositoryURL(required(values.repository, '源仓库'), hostname);
  const commit = required(values.base_commit, '基准提交');
  if (!COMMIT.test(commit)) throw new Error('基准提交需要完整的 40 位 Git commit SHA，不能使用分支名。');
  const writePaths = lines(values.write_paths);
  if (!writePaths.length) throw new Error('至少指定一个允许修改的相对目录路径。');
  writePaths.forEach(path => safeRelativePath(path, true));
  const agents = values.agents;
  if (!Array.isArray(agents) || !agents.length || agents.some(agent => !AGENTS.includes(agent))) throw new Error('至少选择一个兼容的 Agent。');
  const timeout = boundedNumber(values.timeout_minutes, 1, 240, '执行时限（分钟）');
  const attempts = boundedNumber(values.max_attempts, 1, 5, '最多尝试次数');
  const commands = lines(values.commands).map((line, index) => {
    let argv;
    try { argv = JSON.parse(line); } catch { throw new Error(`第 ${index + 1} 条验收命令不是有效的 JSON 参数数组。`); }
    if (!Array.isArray(argv) || !argv.length || argv.some(arg => typeof arg !== 'string' || !arg.trim() || arg.includes('\0'))) throw new Error(`第 ${index + 1} 条验收命令必须是非空字符串组成的 JSON 参数数组。`);
    return argv;
  });
  const outputs = lines(values.required_outputs);
  if (!outputs.length) throw new Error('至少指定一个必需的交付文件。');
  outputs.forEach(path => safeRelativePath(path));
  let resources = [];
  const destinations = new Set();
  if (String(values.resources || '').trim()) {
    try { resources = JSON.parse(values.resources); } catch { throw new Error('参考资源需要有效的 JSON 数组。'); }
    if (!Array.isArray(resources)) throw new Error('参考资源需要有效的 JSON 数组。');
    resources = resources.map((resource, index) => {
      if (!resource || resource.type !== 'git_file') throw new Error(`资源 ${index + 1} 仅支持 git_file 类型。`);
      const url = repositoryURL(resource.repository, hostname);
      if (!COMMIT.test(resource.commit)) throw new Error(`资源 ${index + 1} 需要 40 位 commit SHA。`);
      if (!SHA256.test(resource.sha256)) throw new Error(`资源 ${index + 1} 需要 64 位 SHA-256。`);
      safeRelativePath(resource.path);
      safeRelativePath(resource.destination);
      if (destinations.has(resource.destination)) throw new Error(`资源 ${index + 1} 的目标路径重复。`);
      destinations.add(resource.destination);
      return { type: 'git_file', repository: url, commit: resource.commit.toLowerCase(), path: resource.path, destination: resource.destination, sha256: resource.sha256.toLowerCase() };
    });
  }
  const category = values.category || 'code';
  const size = values.size || 'M';
  const priority = values.priority || 'normal';
  if (!Object.hasOwn(CATEGORIES, category) || !['S', 'M', 'L'].includes(size) || !['normal', 'high'].includes(priority)) throw new Error('任务分类、规模或优先级无效。');
  const reviewNotes = String(values.review_notes || '').trim();
  if (reviewNotes.includes('\0')) throw new Error('验收备注不能包含空字符。');
  const task = {
    schema_version: 1, task_id: taskId, revision: 1, mode: 'subtask', title, prompt, delegation_reason: reason,
    source: { repository, base_commit: commit.toLowerCase(), workspace_patch: null, write_paths: [...new Set(writePaths)] },
    resources, execution: { compatible_agents: [...new Set(agents)], timeout_seconds: timeout * 60, max_attempts: attempts },
    acceptance: { commands, required_outputs: [...new Set(outputs)], review_notes: reviewNotes },
    category, size, priority,
  };
  if (new TextEncoder().encode(JSON.stringify(task)).byteLength > 48 * 1024) throw new Error('任务文件超过 48 KB，请精简说明或资源。');
  return task;
}

export function relativeTime(value, now = Date.now()) {
  const timestamp = typeof value === 'number' ? value * 1000 : Date.parse(value);
  if (!Number.isFinite(timestamp)) return '时间未知';
  const minutes = Math.max(0, Math.floor((now - timestamp) / 60000));
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
  if (minutes < 10080) return `${Math.floor(minutes / 1440)} 天前`;
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric' }).format(timestamp);
}
