export const STATUS = {
  open: { label: 'Open', tone: 'open' },
  claimed: { label: 'Claimed', tone: 'active' },
  running: { label: 'Running', tone: 'active' },
  submitted: { label: 'Awaiting review', tone: 'review' },
  accepted: { label: 'Completed', tone: 'done' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
  failed: { label: 'Failed', tone: 'failed' },
};
export const CATEGORIES = { code: 'Code', docs: 'Docs', research: 'Research' };
const AGENTS = ['codex', 'claude'];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const COMMIT = /^[0-9a-f]{40}$/i;
const SHA256 = /^[0-9a-f]{64}$/i;
const forbiddenControl = /[\u0000-\u001f\u007f]/;

export function validateHostname(value) {
  if (typeof value !== 'string' || !value || value.length > 253 || !value.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label))) {
    throw new Error('Invalid GitHub hostname. Enter a domain without a protocol, port, or path.');
  }
  return value.toLowerCase();
}

export function validateRepository(value) {
  if (typeof value !== 'string' || value.length > 200 || !/^[a-z0-9][a-z0-9-]*\/[a-z0-9_.-]+$/i.test(value) || ['.', '..'].includes(value.split('/')[1])) {
    throw new Error('Repository must use the owner/repo format.');
  }
  return value;
}

function issueNumber(value) {
  if (!Number.isSafeInteger(value) || value < 1) throw new Error('Issue number must be a positive integer.');
  return value;
}

export function safeRelativePath(value, allowDirectory = false) {
  if (typeof value !== 'string' || !value || value.length > 1024 || forbiddenControl.test(value) || /[\\%?#:]/.test(value) || value.startsWith('/')) throw new Error('Path must be relative and must not contain .. segments.');
  const path = allowDirectory && value.endsWith('/') ? value.slice(0, -1) : value;
  if (!path || path.split('/').some(part => !part || part === '.' || part === '..' || part.toLowerCase() === '.git')) throw new Error('Path must be relative and must not contain .. segments or Git metadata.');
  return value;
}

export function githubURL(snapshot, path = '') {
  const host = validateHostname(snapshot.hostname);
  const repo = validateRepository(snapshot.repository);
  if (path) safeRelativePath(path);
  return `https://${host}/${repo}${path ? `/${path.split('/').map(encodeURIComponent).join('/')}` : ''}`;
}

export function taskRunURL(snapshot, number, agent) {
  if (agent !== undefined && !AGENTS.includes(agent)) throw new Error('Choose a compatible Agent.');
  const params = new URLSearchParams({ repo: validateRepository(snapshot.repository), issue: String(issueNumber(number)), hostname: validateHostname(snapshot.hostname) });
  if (agent !== undefined) params.set('agent', agent);
  return `taskboard://run?${params}`;
}

export function taskCommand(snapshot, number, agent = 'codex') {
  if (!AGENTS.includes(agent)) throw new Error('Choose a compatible Agent.');
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
  if (!safe) throw new Error('Source repository must be a valid HTTPS GitHub repository URL.');
  const url = new URL(safe);
  if (url.hostname !== validateHostname(hostname) || url.search || url.hash || url.pathname.endsWith('/') || url.pathname.endsWith('.git')) throw new Error('Source repository must use the current GitHub hostname and an owner/repo URL.');
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

export function executionDuration(seconds) {
  if (!Number.isSafeInteger(seconds) || seconds < 1) return 'Not specified';
  if (seconds < 60) return `${seconds} sec`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder ? `${minutes} min ${remainder} sec` : `${minutes} min`;
}

export function taskLeaderboards(snapshot) {
  const empty = { published: [], completed: [] };
  if (snapshot.demo || !Array.isArray(snapshot.tasks)) return empty;
  const published = new Map();
  const completed = new Map();
  const seen = new Set();
  const current = [...snapshot.tasks].sort((a, b) => (b.spec?.revision || 0) - (a.spec?.revision || 0) || b.number - a.number);
  function add(counts, value) {
    if (typeof value !== 'string' || !value.trim()) return;
    const actor = value.trim().toLowerCase();
    counts.set(actor, (counts.get(actor) || 0) + 1);
  }
  for (const task of current) {
    const id = task.spec?.task_id;
    if (typeof id !== 'string' || !UUID.test(id) || !Object.hasOwn(STATUS, task.status) || seen.has(id.toLowerCase())) continue;
    seen.add(id.toLowerCase());
    if (task.status === 'cancelled') continue;
    add(published, task.author);
    if (task.status === 'accepted') add(completed, task.attempt?.actor);
  }
  const rank = counts => [...counts].map(([actor, count]) => ({ actor, count }))
    .sort((a, b) => b.count - a.count || (a.actor < b.actor ? -1 : a.actor > b.actor ? 1 : 0)).slice(0, 10);
  return { published: rank(published), completed: rank(completed) };
}

export function normalizeSnapshot(data) {
  if (!data || data.schema_version !== 1 || !Array.isArray(data.tasks) || typeof data.demo !== 'boolean' || typeof data.generated_at !== 'string') throw new Error('Incompatible task snapshot. Expected schema_version 1.');
  const hostname = validateHostname(data.hostname);
  if (data.repository !== '') validateRepository(data.repository);
  if (data.generated_at && !Number.isFinite(Date.parse(data.generated_at))) throw new Error('Task snapshot has an invalid sync time.');
  const seen = new Set();
  for (const task of data.tasks) {
    if (!task || !Object.hasOwn(STATUS, task.status) || !task.spec || typeof task.spec.title !== 'string' || typeof task.spec.prompt !== 'string' || !Array.isArray(task.spec.execution?.compatible_agents) || !task.spec.execution.compatible_agents.length || task.spec.execution.compatible_agents.some(agent => !AGENTS.includes(agent))) throw new Error('Task snapshot contains an invalid task. Regenerate the snapshot.');
    issueNumber(task.number);
    if (seen.has(task.number)) throw new Error('Task snapshot contains duplicate Issue numbers.');
    seen.add(task.number);
  }
  if (data.tasks.length && !data.repository) throw new Error('Task snapshot is missing its repository.');
  return { ...data, hostname };
}

function required(value, label, max = 40000) {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`Enter ${label.toLowerCase()}.`);
  if (value.trim().length > max) throw new Error(`${label} is too long. The task file must be smaller than 48 KB.`);
  if (value.includes('\0')) throw new Error(`${label} must not contain null characters.`);
  return value.trim();
}

function lines(value) {
  return String(value || '').split('\n').map(line => line.trim()).filter(Boolean);
}

function boundedNumber(value, min, max, label) {
  const number = Number(value);
  if (!Number.isInteger(number) || number < min || number > max) throw new Error(`${label} must be an integer from ${min} to ${max}.`);
  return number;
}

export function buildTaskExport(values, { hostname = 'github.com', taskId } = {}) {
  if (!UUID.test(taskId)) throw new Error('Invalid task ID. Reopen the publishing form.');
  const title = required(values.title, 'Task title', 200);
  const prompt = required(values.prompt, 'Task instructions');
  const reason = required(values.delegation_reason, 'Delegation reason', 4000);
  const repository = repositoryURL(required(values.repository, 'Source repository'), hostname);
  const commit = required(values.base_commit, 'Base commit');
  if (!COMMIT.test(commit)) throw new Error('Base commit must be a full 40-character Git commit SHA, not a branch name.');
  const writePaths = lines(values.write_paths);
  if (!writePaths.length) throw new Error('Specify at least one relative directory path that may be modified.');
  writePaths.forEach(path => safeRelativePath(path, true));
  const agents = values.agents;
  if (!Array.isArray(agents) || !agents.length || agents.some(agent => !AGENTS.includes(agent))) throw new Error('Choose at least one compatible Agent.');
  const timeout = boundedNumber(values.timeout_minutes, 1, 240, 'Execution time limit (minutes)');
  const attempts = boundedNumber(values.max_attempts, 1, 5, 'Maximum attempts');
  const commands = lines(values.commands).map((line, index) => {
    let argv;
    try { argv = JSON.parse(line); } catch { throw new Error(`Verification command ${index + 1} must be a valid JSON argument array.`); }
    if (!Array.isArray(argv) || !argv.length || argv.some(arg => typeof arg !== 'string' || !arg.trim() || arg.includes('\0'))) throw new Error(`Verification command ${index + 1} must be a JSON argument array of nonempty strings.`);
    return argv;
  });
  const outputs = lines(values.required_outputs);
  if (!outputs.length) throw new Error('Specify at least one required output file.');
  outputs.forEach(path => safeRelativePath(path));
  let resources = [];
  const destinations = new Set();
  if (String(values.resources || '').trim()) {
    try { resources = JSON.parse(values.resources); } catch { throw new Error('Reference resources must be a valid JSON array.'); }
    if (!Array.isArray(resources)) throw new Error('Reference resources must be a valid JSON array.');
    resources = resources.map((resource, index) => {
      if (!resource || resource.type !== 'git_file') throw new Error(`Resource ${index + 1} must use the git_file type.`);
      const url = repositoryURL(resource.repository, hostname);
      if (!COMMIT.test(resource.commit)) throw new Error(`Resource ${index + 1} requires a 40-character commit SHA.`);
      if (!SHA256.test(resource.sha256)) throw new Error(`Resource ${index + 1} requires a 64-character SHA-256.`);
      safeRelativePath(resource.path);
      safeRelativePath(resource.destination);
      if (destinations.has(resource.destination)) throw new Error(`Resource ${index + 1} has a duplicate destination path.`);
      destinations.add(resource.destination);
      return { type: 'git_file', repository: url, commit: resource.commit.toLowerCase(), path: resource.path, destination: resource.destination, sha256: resource.sha256.toLowerCase() };
    });
  }
  const category = values.category || 'code';
  const size = values.size || 'M';
  const priority = values.priority || 'normal';
  if (!Object.hasOwn(CATEGORIES, category) || !['S', 'M', 'L'].includes(size) || !['normal', 'high'].includes(priority)) throw new Error('Invalid task category, size, or priority.');
  const reviewNotes = String(values.review_notes || '').trim();
  if (reviewNotes.includes('\0')) throw new Error('Review notes must not contain null characters.');
  const task = {
    schema_version: 1, task_id: taskId, revision: 1, mode: 'subtask', title, prompt, delegation_reason: reason,
    source: { repository, base_commit: commit.toLowerCase(), workspace_patch: null, write_paths: [...new Set(writePaths)] },
    resources, execution: { compatible_agents: [...new Set(agents)], timeout_seconds: timeout * 60, max_attempts: attempts },
    acceptance: { commands, required_outputs: [...new Set(outputs)], review_notes: reviewNotes },
    category, size, priority,
  };
  if (new TextEncoder().encode(JSON.stringify(task)).byteLength > 48 * 1024) throw new Error('Task file exceeds 48 KB. Shorten the instructions or reduce the resources.');
  return task;
}

export function relativeTime(value, now = Date.now()) {
  const timestamp = typeof value === 'number' ? value * 1000 : Date.parse(value);
  if (!Number.isFinite(timestamp)) return 'Unknown time';
  const minutes = Math.max(0, Math.floor((now - timestamp) / 60000));
  if (minutes < 1) return 'Just now';
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} hr ago`;
  if (minutes < 10080) return `${Math.floor(minutes / 1440)} ${minutes < 2880 ? 'day' : 'days'} ago`;
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(timestamp);
}
