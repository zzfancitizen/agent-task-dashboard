import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createDownloadPackage, buildDownloadRequest, storedZip, publisherInstructions } from '../site/downloads.mjs';

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const runtime = encoder.encode('A test runtime ZIP fixture, never executed.');
const runtimeHash = createHash('sha256').update(runtime).digest('hex');
const task = { number: 42, status: 'open', digest: 'a'.repeat(64), spec: { task_id: '00000000-0000-4000-8000-000000000001', revision: 7, title: '$(touch BAD); `whoami`\n<&>', prompt: 'powershell -EncodedCommand evil', execution: { compatible_agents: ['codex'] } } };
const snapshot = { schema_version: 1, hostname: 'github.acme.internal', repository: 'acme/task-board', demo: false, tasks: [task] };
const starterText = { windows: '@echo off\r\npython bootstrap.py\r\n', macos: '#!/bin/sh\npython3 bootstrap.py\n', linux: '#!/bin/sh\npython3 bootstrap.py\n' };
const starterNames = { windows: 'Start-Taskboard.cmd', macos: 'Start-Taskboard.command', linux: 'Start-Taskboard.sh' };

function assets(overrides = {}) {
  const responses = {
    'runtime.json': JSON.stringify({ schema_version: 1, file: 'taskboard-runtime.zip', sha256: runtimeHash, size: runtime.length }),
    'taskboard-runtime.zip': runtime, 'bootstrap.py': 'print("bootstrap fixture")\n',
    ...Object.fromEntries(Object.keys(starterNames).map(platform => [starterNames[platform], starterText[platform]])),
    ...overrides,
  };
  return async (url, options) => {
    assert.equal(new URL(url).origin, 'https://pages.acme.internal');
    assert.equal(new URL(url).pathname.startsWith('/guild/board/downloads/'), true, 'Pages subdirectory must remain part of every request');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.redirect, 'error');
    const content = responses[new URL(url).pathname.split('/').at(-1)];
    return content === undefined ? new Response('missing', { status: 404 }) : new Response(content);
  };
}
const options = overrides => ({ pageURL: 'https://pages.acme.internal/guild/board/?demo=0', fetchImpl: assets(overrides) });

// Parse ZIP headers independently; CRC is checked with a bit-by-bit implementation.
function unzipStored(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const end = bytes.length - 22;
  assert.equal(view.getUint32(end, true), 0x06054b50);
  const count = view.getUint16(end + 10, true);
  let offset = view.getUint32(end + 16, true);
  const files = new Map();
  for (let index = 0; index < count; index += 1) {
    assert.equal(view.getUint32(offset, true), 0x02014b50);
    assert.equal(view.getUint16(offset + 4, true) >>> 8, 3, 'Unix creator is required for executable modes');
    assert.equal(view.getUint16(offset + 10, true), 0, 'ZIP entries must use the stored method');
    const size = view.getUint32(offset + 24, true);
    assert.equal(size, view.getUint32(offset + 20, true));
    const nameLength = view.getUint16(offset + 28, true);
    const name = decoder.decode(bytes.subarray(offset + 46, offset + 46 + nameLength));
    const local = view.getUint32(offset + 42, true);
    assert.equal(view.getUint32(local, true), 0x04034b50);
    assert.equal(view.getUint16(local + 6, true), 0x0800);
    assert.equal(view.getUint16(local + 12, true), 33, 'fixed 1980-01-01 date');
    assert.equal(view.getUint32(local + 22, true), size);
    assert.equal(decoder.decode(bytes.subarray(local + 30, local + 30 + nameLength)), name);
    const data = bytes.subarray(local + 30 + nameLength, local + 30 + nameLength + size);
    let crc = 0xffffffff;
    for (const byte of data) {
      crc ^= byte;
      for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
    const expectedCRC = (crc ^ 0xffffffff) >>> 0;
    assert.equal(view.getUint32(offset + 16, true), expectedCRC, name);
    assert.equal(view.getUint32(local + 14, true), expectedCRC, name);
    const mode = view.getUint32(offset + 38, true) >>> 16;
    files.set(name, { data, mode });
    offset += 46 + nameLength + view.getUint16(offset + 30, true) + view.getUint16(offset + 32, true);
  }
  assert.equal(offset, end, 'central directory must end exactly at EOCD');
  return files;
}

test('stored ZIP preserves CRC32, UTF-8, fixed dates and regular executable file modes', () => {
  const entries = [{ name: 'Start.command', data: '#!/bin/sh\necho safe\n', executable: true }, { name: '说明.txt', data: '123456789' }];
  const bytes = storedZip(entries);
  const files = unzipStored(bytes);
  assert.equal(files.get('Start.command').mode, 0o100755);
  assert.equal(files.get('说明.txt').mode, 0o100644);
  assert.equal(decoder.decode(files.get('说明.txt').data), '123456789');
  assert.deepEqual(storedZip(entries), bytes, 'same input must make deterministic archive bytes');
  for (const name of ['../escape', '/absolute', 'a\\b', 'a/../b', '.git/config']) assert.throws(() => storedZip([{ name, data: 'x' }]));
  assert.throws(() => storedZip([{ name: 'a.txt', data: '' }, { name: 'A.txt', data: '' }]), /duplicate/i);
});

test('legacy task downloads need no new handoff and pin the confirmed revision without executable task text', () => {
  assert.equal(Object.hasOwn(task.spec, 'handoff'), false);
  assert.deepEqual(buildDownloadRequest(snapshot, { task }, runtimeHash), {
    schema_version: 1, hostname: 'github.acme.internal', repository: 'acme/task-board', issue: 42,
    revision: 7, task_digest: 'a'.repeat(64), runtime_sha256: runtimeHash, action: 'run',
  });
  for (const changed of [{ ...task, number: 43 }, { ...task, digest: 'b'.repeat(64) }, { ...task, spec: { ...task.spec, revision: 8 } }, { ...task, status: 'accepted' }]) {
    assert.throws(() => buildDownloadRequest(snapshot, { task: changed }, runtimeHash));
  }
  for (const invalid of [{ ...snapshot, demo: true }, { ...snapshot, hostname: 'github.com;echo BAD' }, { ...snapshot, repository: 'acme/$(touch)' }]) {
    assert.throws(() => buildDownloadRequest(invalid, { task }, runtimeHash));
  }
  assert.throws(() => buildDownloadRequest(snapshot, { task }, 'not-a-hash'));
});

for (const platform of ['windows', 'macos', 'linux']) test(`${platform} package includes exact starter bytes and pinned JSON, with no task string in code`, async () => {
  const result = await createDownloadPackage({ snapshot, task, platform }, options());
  assert.equal(result.filename, `taskboard-task-42-${platform}.zip`);
  const files = unzipStored(result.bytes);
  assert.deepEqual([...files.keys()].sort(), ['READ-ME.txt', starterNames[platform], 'bootstrap.py', 'request.json', 'runtime.zip'].sort());
  assert.equal(decoder.decode(files.get(starterNames[platform]).data), starterText[platform]);
  assert.equal(files.get(starterNames[platform]).mode, platform === 'windows' ? 0o100644 : 0o100755);
  assert.deepEqual(files.get('runtime.zip').data, runtime);
  const request = JSON.parse(decoder.decode(files.get('request.json').data));
  assert.equal(request.issue, 42);
  assert.equal(request.revision, 7);
  assert.equal(request.hostname, 'github.acme.internal');
  assert.equal(request.runtime_sha256, runtimeHash);
  for (const file of files.values()) assert.equal(decoder.decode(file.data).includes('touch BAD'), false);
  assert.equal(result.request.action, 'run');
});

test('publisher setup uses a board-only descriptor and a separate package action', async () => {
  const result = await createDownloadPackage({ snapshot: { ...snapshot, tasks: [] }, action: 'install-publisher', platform: 'macos' }, options());
  const files = unzipStored(result.bytes);
  assert.equal(result.filename, 'taskboard-publisher-macos.zip');
  assert.deepEqual(JSON.parse(decoder.decode(files.get('request.json').data)), {
    schema_version: 1, hostname: snapshot.hostname, repository: snapshot.repository,
    runtime_sha256: runtimeHash, action: 'install-publisher',
  });
});

test('bad runtime digest, length, manifest URL and missing starter prevent any package result', async () => {
  await assert.rejects(createDownloadPackage({ snapshot, task, platform: 'linux' }, options({ 'taskboard-runtime.zip': encoder.encode('tampered') })), /size|SHA-256/);
  await assert.rejects(createDownloadPackage({ snapshot, task, platform: 'linux' }, options({ 'runtime.json': JSON.stringify({ schema_version: 1, file: 'taskboard-runtime.zip', sha256: 'b'.repeat(64), size: runtime.length }) })), /SHA-256/);
  await assert.rejects(createDownloadPackage({ snapshot, task, platform: 'linux' }, options({ 'runtime.json': JSON.stringify({ schema_version: 1, file: 'https://evil.example/runtime.zip', sha256: runtimeHash, size: runtime.length }) })), /manifest/);
  await assert.rejects(createDownloadPackage({ snapshot, task, platform: 'linux' }, options({ 'Start-Taskboard.sh': undefined })), /Download assets|HTTP 404/);
  await assert.rejects(createDownloadPackage({ snapshot, task, platform: 'linux' }, options({ 'bootstrap.py': 'x'.repeat(300000) })), /too large|size/);
});

test('network or blocked sign-in redirects identify the failing asset without echoing credentials', async () => {
  const fetchImpl = async () => { throw new TypeError('Failed to fetch; private_token=not-for-display'); };
  await assert.rejects(createDownloadPackage({ snapshot, task, platform: 'macos' }, { ...options(), fetchImpl }), error => {
    assert.match(error.message, /runtime\.json/);
    assert.match(error.message, /sign-in|SSO/i);
    assert.doesNotMatch(error.message, /private_token/);
    assert.equal(error.assetURL, 'https://pages.acme.internal/guild/board/downloads/runtime.json');
    return true;
  });
});

for (const asset of ['runtime.json', 'bootstrap.py', 'Start-Taskboard.command']) {
  test(`an HTML login response for ${asset} is never packaged as executable content`, async () => {
    const normal = assets();
    const fetchImpl = (url, opts) => new URL(url).pathname.endsWith('/' + asset)
      ? Promise.resolve(new Response('<!doctype html><html><body>Sign in</body></html>', { headers: { 'Content-Type': 'text/html; charset=utf-8' } }))
      : normal(url, opts);
    await assert.rejects(createDownloadPackage({ snapshot, task, platform: 'macos' }, { ...options(), fetchImpl }), error => {
      assert.ok(error.message.includes(asset));
      assert.match(error.message, /HTML|sign-in/i);
      assert.ok(error.assetURL.endsWith('/' + asset));
      return true;
    });
  });
}

test('an interrupted asset stream retains the failed file location', async () => {
  const normal = assets();
  const fetchImpl = (url, opts) => new URL(url).pathname.endsWith('/bootstrap.py')
    ? Promise.resolve(new Response(new ReadableStream({ start(controller) { controller.error(new TypeError('connection closed')); } })))
    : normal(url, opts);
  await assert.rejects(createDownloadPackage({ snapshot, task, platform: 'linux' }, { ...options(), fetchImpl }), error => {
    assert.match(error.message, /bootstrap\.py/);
    assert.ok(error.assetURL.endsWith('/bootstrap.py'));
    return true;
  });
});

for (const [asset, page] of [
  ['bootstrap.py', '<!-- Gateway sign-in -->\n<!doctype html><html>Sign in</html>'],
  ['Start-Taskboard.command', '<?xml version="1.0"?>\n<html xmlns="http://www.w3.org/1999/xhtml">Sign in</html>'],
]) {
  test(`mislabeled login markup with a preamble is rejected for ${asset}`, async () => {
    const normal = assets();
    const fetchImpl = (url, opts) => new URL(url).pathname.endsWith('/' + asset)
      ? Promise.resolve(new Response(page, { headers: { 'Content-Type': 'text/plain' } }))
      : normal(url, opts);
    await assert.rejects(createDownloadPackage({ snapshot, task, platform: 'macos' }, { ...options(), fetchImpl }), error => {
      assert.match(error.message, /HTML|sign-in/i);
      assert.ok(error.assetURL.endsWith('/' + asset));
      return true;
    });
  });
}

test('demo, unavailable board and stale task downloads fail before fetching assets', async () => {
  const failFetch = async () => assert.fail('invalid request must not fetch runtime');
  for (const board of [{ ...snapshot, demo: true }, { ...snapshot, repository: '' }, { ...snapshot, tasks: [] }]) {
    await assert.rejects(createDownloadPackage({ snapshot: board, task, platform: 'linux' }, { ...options(), fetchImpl: failFetch }));
  }
  await assert.rejects(createDownloadPackage({ snapshot, task, platform: 'unknown' }, { ...options(), fetchImpl: failFetch }));
});

test('copyable publisher instructions bind the real board and require affirmative proposal approval', () => {
  const instruction = publisherInstructions(snapshot, 'https://pages.acme.internal/guild/board/');
  assert.equal(instruction.includes('https://github.acme.internal/acme/task-board'), true);
  assert.equal(instruction.includes('https://pages.acme.internal/guild/board/downloads/'), true);
  assert.match(instruction, /explicit approval/);
  assert.match(instruction, /complete context/);
  assert.match(instruction, /self-contained/);
  for (const field of ['first_step', 'inputs', 'completion', 'blocking_questions', 'execution_prompt']) assert.ok(instruction.includes(field), `Missing author-review guidance: ${field}`);
  assert.throws(() => publisherInstructions({ ...snapshot, demo: true }, 'https://pages.acme.internal/guild/board/'));
});

const deployments = [
  { name: 'a different GitHub owner and project', hostname: 'github.com', repository: 'northwind/agent-relay', pageURL: 'https://northwind.github.io/agent-relay/', repositoryURL: 'https://github.com/northwind/agent-relay', downloadsURL: 'https://northwind.github.io/agent-relay/downloads/' },
  { name: 'a renamed project with query and fragment', hostname: 'github.com', repository: 'contoso/guild-board', pageURL: 'https://contoso.github.io/guild-board/?view=open#publish', repositoryURL: 'https://github.com/contoso/guild-board', downloadsURL: 'https://contoso.github.io/guild-board/downloads/' },
  { name: 'user Pages at the domain root', hostname: 'github.com', repository: 'octocat/octocat.github.io', pageURL: 'https://octocat.github.io/', repositoryURL: 'https://github.com/octocat/octocat.github.io', downloadsURL: 'https://octocat.github.io/downloads/' },
  { name: 'organization Pages at the domain root', hostname: 'github.com', repository: 'northwind/northwind.github.io', pageURL: 'https://northwind.github.io/?view=open#publish', repositoryURL: 'https://github.com/northwind/northwind.github.io', downloadsURL: 'https://northwind.github.io/downloads/' },
  { name: 'enterprise GitHub and a separate Pages host', hostname: 'git.corp.test', repository: 'treasury/work-board', pageURL: 'https://pages.corp.test/teams/treasury/work-board/', repositoryURL: 'https://git.corp.test/treasury/work-board', downloadsURL: 'https://pages.corp.test/teams/treasury/work-board/downloads/' },
  { name: 'a custom Pages domain', hostname: 'github.com', repository: 'northwind/internal-guild', pageURL: 'https://guild.northwind.example/', repositoryURL: 'https://github.com/northwind/internal-guild', downloadsURL: 'https://guild.northwind.example/downloads/' },
  { name: 'an explicit index.html on a custom domain subpath', hostname: 'git.corp.test', repository: 'operations/relay', pageURL: 'https://staff.example.org/tools/relay/index.html?view=open#publish', repositoryURL: 'https://git.corp.test/operations/relay', downloadsURL: 'https://staff.example.org/tools/relay/downloads/' },
];

for (const deployment of deployments) test(`publisher text and downloaded packages follow ${deployment.name}`, async () => {
  const board = { ...snapshot, hostname: deployment.hostname, repository: deployment.repository };
  const instruction = publisherInstructions(board, deployment.pageURL);
  assert.equal(instruction.match(/^Task board repository: (.+)$/m)?.[1], deployment.repositoryURL);
  assert.equal(instruction.match(/^Download assets: (.+)$/m)?.[1], deployment.downloadsURL);

  for (const action of ['run', 'install-publisher']) {
    const expectedAssets = new Map([
      [`${deployment.downloadsURL}runtime.json`, JSON.stringify({ schema_version: 1, file: 'taskboard-runtime.zip', sha256: runtimeHash, size: runtime.length })],
      [`${deployment.downloadsURL}taskboard-runtime.zip`, runtime],
      [`${deployment.downloadsURL}bootstrap.py`, 'print("bootstrap fixture")\n'],
      [`${deployment.downloadsURL}Start-Taskboard.command`, starterText.macos],
    ]);
    const fetched = [];
    const result = await createDownloadPackage({ snapshot: board, task, action, platform: 'macos' }, {
      pageURL: deployment.pageURL,
      fetchImpl: async (url, fetchOptions) => {
        fetched.push(String(url));
        assert.equal(fetchOptions.credentials, 'same-origin');
        const response = expectedAssets.get(String(url));
        return response === undefined ? new Response('wrong deployment path', { status: 404 }) : new Response(response);
      },
    });
    assert.deepEqual(fetched.sort(), [...expectedAssets.keys()].sort());
    const files = unzipStored(result.bytes);
    const request = JSON.parse(decoder.decode(files.get('request.json').data));
    assert.equal(request.hostname, deployment.hostname);
    assert.equal(request.repository, deployment.repository);
    assert.equal(request.action, action);
    assert.equal(request.runtime_sha256, runtimeHash);
    assert.equal(decoder.decode(files.get('READ-ME.txt').data).match(/^Task repository: (.+)$/m)?.[1], deployment.repositoryURL);
    if (action === 'run') {
      assert.equal(request.issue, task.number);
      assert.equal(request.revision, task.spec.revision);
      assert.equal(request.task_digest, task.digest);
    } else {
      assert.equal(Object.hasOwn(request, 'issue'), false);
    }
  }
});
