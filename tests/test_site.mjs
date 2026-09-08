import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as model from '../site/model.mjs';
import {
  validateHostname, validateRepository, githubURL, taskRunURL, taskCommand,
  safeHTTPS, resourceURL, filterTasks, taskCounts, normalizeSnapshot, buildTaskExport, taskLeaderboards, executionDuration,
} from '../site/model.mjs';

const snapshot = { schema_version: 1, repository: 'acme/task-board', hostname: 'github.acme.internal', generated_at: '2026-09-08T09:00:00Z', demo: false, tasks: [] };
const makeTask = (number, status, title, agents, category = 'code') => ({ number, status, author: 'lin', spec: { title, prompt: '检查边界条件', execution: { compatible_agents: agents }, category } });
const tasks = [makeTask(1, 'open', '补充 Date 测试', ['codex']), makeTask(2, 'running', '整理文档', ['claude'], 'docs'), makeTask(3, 'submitted', '调研队列', ['codex', 'claude'], 'research'), makeTask(4, 'accepted', '文档校验', ['codex'], 'docs')];
const handoff = {
  non_goals: ['不改动日期工具的公开接口。'], constraints: ['保留已有日期格式。'], assumptions: [],
  environment: 'Use Python 3.13 and Git. Tests use the standard library and require no external services.',
  stop_conditions: ['If the pinned source is missing, stop and report the missing input.'],
  review: {
    first_step: 'Read tests/test_dates.py and the date formatting code at the pinned source commit.',
    inputs: 'The source repository contains the date helper and existing test fixtures in tests/.',
    completion: 'Run both declared commands; return changes.patch, summary.md and verification.json.',
    blocking_questions: [],
  },
};
const form = {
  title: '验证日期边界', prompt: '为日期工具补充边界测试。', delegation_reason: '输入独立，验收明确。',
  repository: 'https://github.acme.internal/acme/source', base_commit: 'a'.repeat(40), write_paths: 'tests/\ndocs/',
  agents: ['codex', 'claude'], timeout_minutes: '30', max_attempts: '2', commands: '["python3","-m","unittest"]\n["git","diff","--check"]',
  required_outputs: 'changes.patch\nsummary.md\nverification.json', review_notes: '检查边界覆盖。', resources: '', category: 'code', size: 'S', priority: 'normal', handoff: JSON.stringify(handoff),
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
  assert.deepEqual(task.handoff, handoff);
  assert.ok(!JSON.stringify(task).includes('session'));
});

test('new manual exports require an author-prepared handoff and block declared missing inputs', () => {
  for (const value of [undefined, '', '  ']) {
    assert.throws(() => buildTaskExport({ ...form, handoff: value }, options), /handoff/i);
  }
  const blocked = { ...handoff, review: { ...handoff.review, blocking_questions: ['Which date format is required?'] } };
  assert.throws(() => buildTaskExport({ ...form, handoff: JSON.stringify(blocked) }, options), /blocking questions/i);
  const task = buildTaskExport({ ...form, handoff: JSON.stringify({ ...handoff, non_goals: [], constraints: [], assumptions: [] }) }, options);
  assert.deepEqual(task.handoff.non_goals, []);
  assert.equal(task.handoff.review.inputs, handoff.review.inputs);
});

test('manual handoff JSON requires exact fields, concrete answers and an explicit stop condition', () => {
  const without = (value, key) => Object.fromEntries(Object.entries(value).filter(([name]) => name !== key));
  const invalid = [
    null, [], without(handoff, 'environment'), { ...handoff, complete: true },
    { ...handoff, constraints: [''] }, { ...handoff, assumptions: 'None' },
    { ...handoff, environment: '   ' }, { ...handoff, environment: 'Python\0Git' },
    { ...handoff, stop_conditions: [] }, { ...handoff, stop_conditions: [null] },
    { ...handoff, review: without(handoff.review, 'inputs') },
    { ...handoff, review: { ...handoff.review, passed: true } },
    { ...handoff, review: { ...handoff.review, first_step: false } },
    { ...handoff, review: { ...handoff.review, completion: '\n' } },
    { ...handoff, review: { ...handoff.review, blocking_questions: [1] } },
  ];
  for (const value of invalid) assert.throws(() => buildTaskExport({ ...form, handoff: JSON.stringify(value) }, options), /handoff/i);
  assert.throws(() => buildTaskExport({ ...form, handoff: '{' }, options), /JSON/);
  const padded = { ...handoff, environment: `  ${handoff.environment}\n` };
  assert.equal(buildTaskExport({ ...form, handoff: JSON.stringify(padded) }, options).handoff.environment, padded.environment, 'the author\'s handoff text must not be rewritten');
});

const handoffTextCases = JSON.parse(readFileSync(new URL('./fixtures/handoff-text.json', import.meta.url), 'utf8'));
for (const { name, text, valid } of handoffTextCases) test(`handoff text matches Python validation: ${name}`, () => {
  const variants = [
    { ...handoff, environment: text },
    { ...handoff, constraints: [text] },
    { ...handoff, review: { ...handoff.review, first_step: text } },
  ];
  for (const value of variants) {
    const exportTask = () => buildTaskExport({ ...form, handoff: JSON.stringify(value) }, options);
    if (valid) assert.deepEqual(exportTask().handoff, value, 'valid Unicode and whitespace must remain unchanged');
    else assert.throws(exportTask, /handoff/i);
  }
});

test('copied prompt preserves authored instructions and carries the full declared execution context', () => {
  assert.equal(typeof model.taskExecutionPrompt, 'function');
  const resource = { type: 'git_file', repository: form.repository, commit: 'b'.repeat(40), path: 'docs/rules.md', destination: 'resources/rules.md', sha256: 'c'.repeat(64) };
  const spec = buildTaskExport({ ...form, resources: JSON.stringify([resource]) }, options);
  spec.prompt = '  检查这些边界。\n\nKeep this quoted example: `date(0)`\n';
  const original = structuredClone(spec);
  const copy = model.taskExecutionPrompt(spec);
  assert.ok(copy.startsWith(spec.prompt), 'the user prompt must retain its whitespace, language and examples');
  for (const input of [spec.title, spec.delegation_reason, spec.source.repository, spec.source.base_commit,
    ...spec.source.write_paths, resource.commit, resource.path, resource.destination, resource.sha256,
    ...spec.acceptance.required_outputs, spec.acceptance.review_notes, spec.handoff.environment,
    spec.handoff.review.first_step, spec.handoff.review.inputs, spec.handoff.review.completion,
    ...spec.handoff.stop_conditions]) assert.ok(copy.includes(input), `Missing declared context: ${input}`);
  assert.match(copy, /"timeout_seconds": 1800/);
  assert.match(copy, /"max_attempts": 2/);
  assert.match(copy, /"compatible_agents"/);
  assert.match(copy, /"commands"/);
  assert.match(copy, /stop and report/i);
  assert.deepEqual(spec, original, 'copying must not rewrite the immutable task');
});

test('legacy canonical tasks remain readable and copyable without inventing a handoff', () => {
  assert.equal(typeof model.taskExecutionPrompt, 'function');
  const spec = buildTaskExport(form, options);
  delete spec.handoff;
  const legacy = { ...tasks[0], spec };
  assert.deepEqual(normalizeSnapshot({ ...snapshot, tasks: [legacy] }).tasks, [legacy]);
  const copy = model.taskExecutionPrompt(spec);
  assert.ok(copy.startsWith(spec.prompt));
  assert.ok(copy.includes(spec.source.base_commit));
  assert.ok(copy.includes(spec.acceptance.required_outputs[0]));
  assert.ok(!copy.includes('"handoff"'));
  assert.equal(Object.hasOwn(spec, 'handoff'), false);
});

test('task export rejects missing authority and shell-string commands with useful errors', () => {
  for (const [key, value, message] of [
    ['base_commit', 'HEAD', /40/], ['write_paths', '../private', /path/i], ['repository', 'https://attacker.example/a/b', /repository/i],
    ['agents', [], /Agent/], ['commands', 'python3 -m unittest', /JSON/], ['commands', '["sh", 1]', /strings/],
    ['timeout_minutes', '241', /240/], ['max_attempts', '6', /5/], ['title', '', /title/],
    ['required_outputs', '../secret', /path/i], ['prompt', 'x'.repeat(50000), /48/],
  ]) assert.throws(() => buildTaskExport({ ...form, [key]: value }, options), message);
});

test('task export validates pinned resource hashes and path traversal', () => {
  const resource = { type: 'git_file', repository: form.repository, commit: 'b'.repeat(40), path: 'docs/rules.md', destination: 'resources/rules.md', sha256: 'c'.repeat(64) };
  assert.deepEqual(buildTaskExport({ ...form, resources: JSON.stringify([resource]) }, options).resources, [resource]);
  assert.throws(() => buildTaskExport({ ...form, resources: JSON.stringify([{ ...resource, sha256: 'wrong' }]) }, options), /SHA-256/);
  assert.throws(() => buildTaskExport({ ...form, resources: JSON.stringify([{ ...resource, destination: '../secret' }]) }, options), /path/i);
});

test('task export rejects Git metadata, duplicate destinations, and invalid protocol strings', () => {
  const resource = { type: 'git_file', repository: form.repository, commit: 'b'.repeat(40), path: 'docs/rules.md', destination: 'resources/rules.md', sha256: 'c'.repeat(64) };
  assert.throws(() => buildTaskExport({ ...form, write_paths: '.git/hooks/' }, options), /path/i);
  assert.throws(() => buildTaskExport({ ...form, required_outputs: 'nested/.GIT/config' }, options), /path/i);
  assert.throws(() => buildTaskExport({ ...form, resources: JSON.stringify([resource, resource]) }, options), /duplicate/);
  assert.throws(() => buildTaskExport({ ...form, commands: '["python3", "   "]' }, options), /strings/);
  assert.throws(() => buildTaskExport({ ...form, review_notes: 'hello\0world' }, options), /null characters/);
});

test('launcher URL carries the chosen compatible provider and rejects unknown providers', () => {
  const url = new URL(taskRunURL(snapshot, 42, 'claude'));
  assert.equal(url.searchParams.get('agent'), 'claude');
  assert.equal(new URL(taskRunURL(snapshot, 42)).searchParams.has('agent'), false);
  assert.throws(() => taskRunURL(snapshot, 42, 'unknown'), /Agent/);
});

const rankedTask = (id, status, author, actor = null, number = id) => ({
  ...makeTask(number, status, `Task ${id}`, ['codex']), author,
  spec: { ...makeTask(number, status, '', ['codex']).spec, task_id: `00000000-0000-4000-8000-${String(id).padStart(12, '0')}`, revision: 1 },
  attempt: actor ? { actor } : null,
});

test('leaderboards count unique published tasks and only accepted work for the executor', () => {
  const records = [
    rankedTask(1, 'open', 'Ada'), rankedTask(2, 'running', 'ADA', 'Bex'),
    rankedTask(3, 'submitted', 'Bo', 'Bex'), rankedTask(4, 'accepted', 'Bo', 'BEX'),
    rankedTask(5, 'accepted', 'Ci', 'ada'), rankedTask(6, 'cancelled', 'Ci', 'Bex'),
    rankedTask(7, 'failed', 'Di', 'Bex'), rankedTask(8, 'claimed', 'Di', 'Bex'),
    rankedTask(4, 'accepted', 'Bo', 'BEX', 44),
  ];
  assert.deepEqual(taskLeaderboards({ ...snapshot, tasks: records }), {
    published: [{ actor: 'ada', count: 2 }, { actor: 'bo', count: 2 }, { actor: 'di', count: 2 }, { actor: 'ci', count: 1 }],
    completed: [{ actor: 'ada', count: 1 }, { actor: 'bex', count: 1 }],
  });
  assert.equal(filterTasks(records, { status: 'open' }).length, 1);
  assert.equal(taskLeaderboards({ ...snapshot, tasks: records }).published[0].count, 2);
});

test('rankings use current revision, deterministic ties, and do not fabricate absent identity or demo results', () => {
  const old = rankedTask(1, 'accepted', 'Ada', 'Bex');
  const cancelled = { ...rankedTask(1, 'cancelled', 'Ada', 'Bex', 11), spec: { ...old.spec, revision: 2 } };
  const missingActor = rankedTask(2, 'accepted', 'ci');
  const records = [old, cancelled, missingActor, rankedTask(3, 'open', 'BO'), rankedTask(4, 'open', 'bo')];
  const expected = { published: [{ actor: 'bo', count: 2 }, { actor: 'ci', count: 1 }], completed: [] };
  assert.deepEqual(taskLeaderboards({ ...snapshot, tasks: records }), expected);
  assert.deepEqual(taskLeaderboards({ ...snapshot, tasks: records.toReversed() }), expected);
  assert.deepEqual(taskLeaderboards({ ...snapshot, tasks: records, demo: true }), { published: [], completed: [] });
  assert.deepEqual(taskLeaderboards(snapshot), { published: [], completed: [] });
  assert.deepEqual(taskLeaderboards({ ...snapshot, tasks: [makeTask(9, 'accepted', 'missing task id', ['codex'])] }), { published: [], completed: [] });
  assert.deepEqual(taskLeaderboards({ ...snapshot, tasks: [{ ...old, spec: { ...old.spec, task_id: [old.spec.task_id] } }] }), { published: [], completed: [] });
});

test('rankings show ten contributors with stable alphabetical order for equal counts', () => {
  const records = Array.from({ length: 12 }, (_, i) => rankedTask(i + 1, 'accepted', `user-${String(i).padStart(2, '0')}`, `user-${String(i).padStart(2, '0')}`));
  const ranks = taskLeaderboards({ ...snapshot, tasks: records.toReversed() });
  assert.equal(ranks.published.length, 10);
  assert.deepEqual(ranks.completed.map(entry => entry.actor), ['user-00', 'user-01', 'user-02', 'user-03', 'user-04', 'user-05', 'user-06', 'user-07', 'user-08', 'user-09']);
});

test('execution duration shows sub-minute timeouts without rounding them to zero', () => {
  for (const [seconds, label] of [[10, '10 sec'], [59, '59 sec'], [60, '1 min'], [90, '1 min 30 sec'], [1800, '30 min'], [0, 'Not specified'], [undefined, 'Not specified']]) {
    assert.equal(executionDuration(seconds), label);
  }
});
