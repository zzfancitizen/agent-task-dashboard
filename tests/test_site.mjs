import test from 'node:test';
import assert from 'node:assert/strict';
import {
  validateHostname, validateRepository, githubURL, taskRunURL, taskCommand,
  safeHTTPS, resourceURL, filterTasks, taskCounts, normalizeSnapshot, buildTaskExport,
} from '../site/model.mjs';

const snapshot = { schema_version: 1, repository: 'acme/task-board', hostname: 'github.acme.internal', generated_at: '2026-09-08T09:00:00Z', demo: false, tasks: [] };
const makeTask = (number, status, title, agents, category = 'code') => ({ number, status, author: 'lin', spec: { title, prompt: '检查边界条件', execution: { compatible_agents: agents }, category } });
const tasks = [makeTask(1, 'open', '补充 Date 测试', ['codex']), makeTask(2, 'running', '整理文档', ['claude'], 'docs'), makeTask(3, 'submitted', '调研队列', ['codex', 'claude'], 'research'), makeTask(4, 'accepted', '文档校验', ['codex'], 'docs')];
const form = {
  title: '验证日期边界', prompt: '为日期工具补充边界测试。', delegation_reason: '输入独立，验收明确。',
  repository: 'https://github.acme.internal/acme/source', base_commit: 'a'.repeat(40), write_paths: 'tests/\ndocs/',
  agents: ['codex', 'claude'], timeout_minutes: '30', max_attempts: '2', commands: '["python3","-m","unittest"]\n["git","diff","--check"]',
  required_outputs: 'changes.patch\nsummary.md\nverification.json', review_notes: '检查边界覆盖。', resources: '', category: 'code', size: 'S', priority: 'normal',
};
const options = { hostname: snapshot.hostname, taskId: 'a6fa0c0e-9410-48ee-a8e7-91f9b07bf871' };

test('validates repository and hostname before constructing any launch URL', () => {
  assert.equal(validateHostname('GitHub.Acme.Internal'), 'github.acme.internal');
  assert.equal(validateRepository('acme/task-board'), 'acme/task-board');
  for (const host of ['https://github.com', 'github.com/evil', 'github.com:443', 'x\ny', '-bad.com', 'a..b']) assert.throws(() => validateHostname(host));
  for (const repo of ['acme/task/extra', '../task', 'acme/..', 'acme/task?x=1', 'a/hello world', 'a/$(whoami)']) assert.throws(() => validateRepository(repo));
});

test('launch URL and CLI command preserve configured enterprise host and issue identity', () => {
  const url = new URL(taskRunURL(snapshot, 42));
  assert.equal(url.protocol, 'taskboard:');
  assert.equal(url.hostname, 'run');
  assert.equal(url.searchParams.get('repo'), 'acme/task-board');
  assert.equal(url.searchParams.get('issue'), '42');
  assert.equal(url.searchParams.get('hostname'), 'github.acme.internal');
  assert.equal(taskCommand(snapshot, 42, 'claude'), 'taskboard --repo acme/task-board --hostname github.acme.internal run 42 --agent claude');
  assert.equal(githubURL(snapshot, 'issues/42'), 'https://github.acme.internal/acme/task-board/issues/42');
  for (const issue of [0, -1, '1;open', 1.2, Number.MAX_SAFE_INTEGER + 1]) assert.throws(() => taskRunURL(snapshot, issue));
  assert.throws(() => taskCommand(snapshot, 1, '$(evil)'));
  assert.throws(() => githubURL(snapshot, '../settings'));
});

test('rejects unsafe resource and artifact links without executing or guessing URLs', () => {
  for (const url of ['javascript:alert(1)', 'data:text/html,hello', 'http://github.com/a', 'https://u:p@github.com/a', 'https://github.com/\nfoo']) assert.equal(safeHTTPS(url), null);
  assert.equal(safeHTTPS('https://artifacts.acme.internal/result.zip'), 'https://artifacts.acme.internal/result.zip');
  const resource = { repository: 'https://github.acme.internal/acme/source', commit: 'b'.repeat(40), path: 'docs/中文 guide.md' };
  assert.equal(resourceURL(resource, snapshot.hostname), `https://github.acme.internal/acme/source/blob/${'b'.repeat(40)}/docs/%E4%B8%AD%E6%96%87%20guide.md`);
  for (const path of ['../secret', '/etc/passwd', 'docs/../../x', 'docs\\x', 'docs/%2e%2e/secret']) assert.equal(resourceURL({ ...resource, path }, snapshot.hostname), null);
  assert.equal(resourceURL({ ...resource, repository: 'https://attacker.example/acme/source' }, snapshot.hostname), null);
});

test('combines search, status, provider and category without mutating source data', () => {
  assert.deepEqual(filterTasks(tasks, { query: 'date', status: 'open', agent: 'codex', category: 'code' }).map(t => t.number), [1]);
  assert.deepEqual(filterTasks(tasks, { query: '文档', category: 'docs' }).map(t => t.number), [2, 4]);
  assert.deepEqual(filterTasks(tasks, { status: 'active', agent: 'claude' }).map(t => t.number), [2]);
  assert.deepEqual(filterTasks(tasks, { query: '#3' }).map(t => t.number), [3]);
  assert.deepEqual(filterTasks(tasks, { agent: 'claude', category: 'code' }), []);
  assert.equal(tasks.length, 4);
});

test('counts derive from actual statuses including claimed work', () => {
  assert.deepEqual(taskCounts([...tasks, makeTask(5, 'claimed', '等待启动', ['codex'])]), { total: 5, open: 1, active: 2, submitted: 1, accepted: 1 });
  assert.deepEqual(taskCounts([]), { total: 0, open: 0, active: 0, submitted: 0, accepted: 0 });
});

test('snapshot validation accepts empty onboarding and rejects unknown or duplicated records', () => {
  assert.deepEqual(normalizeSnapshot(snapshot), snapshot);
  assert.throws(() => normalizeSnapshot({ ...snapshot, tasks: [{ ...tasks[0], status: 'surprise' }] }));
  assert.throws(() => normalizeSnapshot({ ...snapshot, tasks: [tasks[0], tasks[0]] }));
  assert.throws(() => normalizeSnapshot({ ...snapshot, schema_version: 2 }));
  assert.equal(normalizeSnapshot({ ...snapshot, repository: '' }).repository, '');
});

test('exports immutable v1 task with argv arrays, bounded execution, and normalized paths', () => {
  const task = buildTaskExport(form, options);
  assert.equal(task.schema_version, 1);
  assert.equal(task.task_id, options.taskId);
  assert.equal(task.mode, 'subtask');
  assert.equal(task.revision, 1);
  assert.equal(task.source.workspace_patch, null);
  assert.deepEqual(task.source.write_paths, ['tests/', 'docs/']);
  assert.deepEqual(task.acceptance.commands, [['python3', '-m', 'unittest'], ['git', 'diff', '--check']]);
  assert.equal(task.execution.timeout_seconds, 1800);
  assert.equal(task.execution.max_attempts, 2);
  assert.deepEqual(task.resources, []);
  assert.ok(!JSON.stringify(task).includes('session'));
});

test('task export rejects missing authority and shell-string commands with useful errors', () => {
  for (const [key, value, message] of [
    ['base_commit', 'HEAD', /40/], ['write_paths', '../private', /路径/], ['repository', 'https://attacker.example/a/b', /仓库/],
    ['agents', [], /Agent/], ['commands', 'python3 -m unittest', /JSON/], ['commands', '["sh", 1]', /字符串/],
    ['timeout_minutes', '241', /240/], ['max_attempts', '6', /5/], ['title', '', /标题/],
    ['required_outputs', '../secret', /路径/], ['prompt', 'x'.repeat(50000), /48/],
  ]) assert.throws(() => buildTaskExport({ ...form, [key]: value }, options), message);
});

test('task export validates pinned resource hashes and path traversal', () => {
  const resource = { type: 'git_file', repository: form.repository, commit: 'b'.repeat(40), path: 'docs/rules.md', destination: 'resources/rules.md', sha256: 'c'.repeat(64) };
  assert.deepEqual(buildTaskExport({ ...form, resources: JSON.stringify([resource]) }, options).resources, [resource]);
  assert.throws(() => buildTaskExport({ ...form, resources: JSON.stringify([{ ...resource, sha256: 'wrong' }]) }, options), /SHA-256/);
  assert.throws(() => buildTaskExport({ ...form, resources: JSON.stringify([{ ...resource, destination: '../secret' }]) }, options), /路径/);
});

test('task export rejects Git metadata, duplicate destinations, and invalid protocol strings', () => {
  const resource = { type: 'git_file', repository: form.repository, commit: 'b'.repeat(40), path: 'docs/rules.md', destination: 'resources/rules.md', sha256: 'c'.repeat(64) };
  assert.throws(() => buildTaskExport({ ...form, write_paths: '.git/hooks/' }, options), /路径/);
  assert.throws(() => buildTaskExport({ ...form, required_outputs: 'nested/.GIT/config' }, options), /路径/);
  assert.throws(() => buildTaskExport({ ...form, resources: JSON.stringify([resource, resource]) }, options), /重复/);
  assert.throws(() => buildTaskExport({ ...form, commands: '["python3", "   "]' }, options), /字符串/);
  assert.throws(() => buildTaskExport({ ...form, review_notes: 'hello\0world' }, options), /空字符/);
});

test('launcher URL carries the chosen compatible provider and rejects unknown providers', () => {
  const url = new URL(taskRunURL(snapshot, 42, 'claude'));
  assert.equal(url.searchParams.get('agent'), 'claude');
  assert.equal(new URL(taskRunURL(snapshot, 42)).searchParams.has('agent'), false);
  assert.throws(() => taskRunURL(snapshot, 42, 'unknown'), /Agent/);
});
