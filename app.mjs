import { STATUS, CATEGORIES, normalizeSnapshot, filterTasks, taskCounts, taskLeaderboards, executionDuration, githubURL, taskCommand, safeHTTPS, resourceURL, buildTaskExport, taskExecutionPrompt, relativeTime } from './model.mjs';
import { DOWNLOAD_PLATFORMS, createDownloadPackage, publisherInstructions } from './downloads.mjs';

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const emptySnapshot = { schema_version: 1, repository: '', hostname: 'github.com', generated_at: '', demo: false, tasks: [] };
let snapshot = emptySnapshot;
let currentMode = new URL(location.href).searchParams.get('demo') === '1' ? 'demo' : 'live';
let filters = { query: '', status: 'all', agent: 'all', category: 'all' };
let toastTimer;
let loading = false;
let loadVersion = 0;
let lastError = null;
let taskId;
let exportedTask = false;

function element(tag, className = '', text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function icon(name, small = false) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  node.setAttribute('class', small ? 'icon small' : 'icon');
  node.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`);
  node.append(use);
  return node;
}
function button(label, className, action, iconName) {
  const node = element('button', className);
  node.type = 'button';
  if (iconName) node.append(icon(iconName));
  node.append(document.createTextNode(label));
  node.addEventListener('click', action);
  return node;
}
function link(label, href, className = '', iconName) {
  const node = element('a', className, label);
  node.href = href;
  node.target = '_blank';
  node.rel = 'noopener noreferrer';
  if (iconName) node.append(icon(iconName, true));
  return node;
}
function setExternalLink(node, href) {
  if (href) {
    node.href = href;
    node.target = '_blank';
    node.rel = 'noopener noreferrer';
    node.removeAttribute('aria-disabled');
  } else {
    node.removeAttribute('href');
    node.setAttribute('aria-disabled', 'true');
  }
}
function statusBadge(status) {
  return element('span', `status-badge ${STATUS[status].tone}`, STATUS[status].label);
}
function agentChip(agent) {
  const chip = element('span', `agent-chip ${agent}`);
  chip.append(element('span', 'agent-glyph', agent === 'claude' ? '✳' : '◉'), document.createTextNode(agent === 'claude' ? 'Claude Code' : 'Codex'));
  return chip;
}
function showToast(message, error = false) {
  const toast = $('#toast');
  const dialogs = $$('dialog[open]');
  (dialogs.at(-1) || document.body).append(toast);
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.toggle('error', error);
  toast.hidden = false;
  toastTimer = setTimeout(() => { toast.hidden = true; }, error ? 7000 : 3200);
}
async function copyText(text, success = 'Copied to clipboard') {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('Clipboard access is unavailable in this browser');
    await navigator.clipboard.writeText(text);
    showToast(success);
  } catch {
    const dialog = element('dialog', 'dialog copy-dialog');
    dialog.setAttribute('aria-labelledby', 'manual-copy-title');
    const header = element('div', 'dialog-header');
    const title = element('h2', '', 'Copy manually');
    title.id = 'manual-copy-title';
    const close = button('', 'icon-button', () => dialog.close(), 'close');
    close.setAttribute('aria-label', 'Close manual copy');
    header.append(title, close);
    const body = element('div', 'dialog-body');
    const field = element('label', 'field', 'Automatic copying is unavailable. Select and copy the text below.');
    const input = element('textarea', 'mono');
    input.value = text;
    input.readOnly = true;
    input.rows = Math.min(12, Math.max(4, text.split('\n').length));
    field.append(input);
    body.append(field);
    dialog.append(header, body);
    document.body.append(dialog);
    dialog.addEventListener('close', () => dialog.remove(), { once: true });
    dialog.showModal();
    input.focus();
    input.select();
  }
}
function openDialog(dialog) { if (!dialog.open) dialog.showModal(); }
function closeDialog(dialog) { dialog.close(); }
function saveDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const download = element('a');
  download.href = url;
  download.download = filename;
  document.body.append(download);
  download.click();
  download.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderCounts() {
  const counts = taskCounts(snapshot.tasks);
  for (const name of ['total', 'open', 'active', 'submitted']) $(`#count-${name}`).textContent = counts[name];
  for (const node of $$('[data-tab-count]')) node.textContent = counts[node.dataset.tabCount];
  $('#nav-count').textContent = counts.total;
}
function renderLeaderboards(state = '') {
  const rankings = taskLeaderboards(snapshot);
  $('#ranking-scope').textContent = state === 'loading' ? 'Loading confirmed tasks…' : state === 'error' ? 'Task data is unavailable' : snapshot.demo ? 'Demo tasks are excluded from rankings' : 'Confirmed tasks · Top 10 in each list · Independent of filters';
  for (const [kind, label] of [['published', 'published'], ['completed', 'completed']]) {
    const list = $(`#leaderboard-${kind}`);
    const entries = state ? [] : rankings[kind];
    if (!entries.length) {
      list.replaceChildren(element('li', 'ranking-empty', state === 'loading' ? 'Loading…' : state === 'error' ? 'Rankings are unavailable. Please refresh later.' : snapshot.demo ? 'Return to the live board to see contributions.' : kind === 'published' ? 'No confirmed publications yet.' : 'No accepted completions yet.'));
      continue;
    }
    list.replaceChildren(...entries.map((entry, index) => {
      const row = element('li', 'ranking-row');
      const position = element('span', 'ranking-position', String(index + 1).padStart(2, '0'));
      position.setAttribute('aria-label', `Rank ${index + 1}`);
      row.append(position, element('span', 'ranking-actor', `@${entry.actor}`), element('strong', 'ranking-count', `${entry.count}`));
      row.setAttribute('aria-label', `Rank ${index + 1}, ${entry.actor}, ${label} ${entry.count}`);
      return row;
    }));
  }
}
function renderWorkspace() {
  $('#demo-banner').hidden = !snapshot.demo;
  $('#workspace-repo').textContent = snapshot.demo ? 'Demo workspace · Fictional tasks' : snapshot.repository || 'Repository not connected';
  $('#workspace-repo').title = snapshot.repository || 'No GitHub task repository configured';
  const repoURL = snapshot.repository && !snapshot.demo ? githubURL(snapshot) : null;
  setExternalLink($('#nav-repository'), repoURL);
  const docs = $('#guide-docs-link');
  docs.hidden = !repoURL;
  if (repoURL) setExternalLink(docs, githubURL(snapshot, 'blob/HEAD/README.en.md'));
  $('#try-demo-footer').textContent = snapshot.demo ? 'Return to live board' : 'Explore the demo';
  $('#try-demo-footer').append(icon('arrow', true));
  $('#sync-status').textContent = snapshot.demo ? 'Demo data · No real executions' : snapshot.repository ? `Synced ${relativeTime(snapshot.generated_at)}` : 'No task repository connected';
  $('#sync-status').title = snapshot.generated_at || 'No sync recorded';
  $('#sync-dot').classList.toggle('muted-dot', !snapshot.repository);
  renderPublisher();
}

function renderTask(task) {
  const spec = task.spec;
  const category = Object.hasOwn(CATEGORIES, spec.category) ? spec.category : 'code';
  const row = element('article', 'task-row');
  row.dataset.issue = task.number;
  const pin = element('span', 'quest-pin');
  pin.setAttribute('aria-hidden', 'true');
  const docket = element('div', 'quest-docket');
  const size = ['S', 'M', 'L'].includes(spec.size) ? spec.size : 'M';
  const rank = element('span', 'quest-rank', { S: '★☆☆', M: '★★☆', L: '★★★' }[size]);
  rank.setAttribute('aria-label', `Task size ${size}`);
  rank.title = `Stars indicate task size ${size}`;
  docket.append(element('span', 'quest-number', `Quest / No. ${String(task.number).padStart(3, '0')}`), rank);
  const categoryIcon = element('span', `task-category-icon ${category}`);
  categoryIcon.append(icon(category === 'docs' ? 'file' : category === 'research' ? 'research' : 'code'));
  const content = element('div', 'task-content');
  const titleLine = element('div', 'task-title-line');
  const title = button(spec.title, 'task-title', () => showTask(task));
  title.setAttribute('aria-label', `View task #${task.number}: ${spec.title}`);
  titleLine.append(title);
  if (spec.priority === 'high') titleLine.append(element('span', 'priority-chip', 'High priority'));
  const description = element('p', 'task-description', spec.prompt);
  const tags = element('div', 'task-tags');
  tags.append(element('span', 'tag', CATEGORIES[category]), element('span', 'tag size', ['S', 'M', 'L'].includes(spec.size) ? spec.size : 'M'));
  for (const agent of spec.execution.compatible_agents) tags.append(agentChip(agent));
  const meta = element('span', 'task-meta');
  const author = String(task.author || 'unknown');
  meta.append(element('span', 'author-avatar', author.slice(0, 2).toUpperCase()), element('span', '', author), element('span', 'meta-dot', '·'), element('span', '', `#${task.number}`), element('span', 'meta-dot', '·'), element('span', '', relativeTime(task.updated_at || task.created_at)));
  tags.append(meta);
  content.append(titleLine, description, tags);
  const actions = element('div', 'task-actions');
  actions.append(statusBadge(task.status));
  if (task.status === 'open') {
    const view = button('View task', 'button ghost compact', () => showTask(task), 'arrow');
    actions.append(view);
  } else {
    const view = button(task.status === 'submitted' || task.status === 'accepted' ? 'View result' : 'View details', 'row-link', () => showTask(task));
    view.append(icon('arrow', true));
    actions.append(view);
  }
  row.append(pin, docket, categoryIcon, content, actions);
  return row;
}
function emptyArt(type = 'box') {
  const art = element('div', 'empty-art');
  art.setAttribute('aria-hidden', 'true');
  art.append(icon(type), element('span', 'art-dot'));
  return art;
}
function renderEmpty() {
  const container = element('div', 'empty-state');
  const filtered = snapshot.tasks.length > 0;
  container.append(emptyArt(filtered ? 'search' : 'box'));
  if (filtered) {
    container.append(element('h2', '', 'No matching tasks'), element('p', '', 'Try another keyword or adjust the filters to find a task.'));
    const actions = element('div', 'empty-actions');
    actions.append(button('Clear filters', 'button secondary', resetFilters, 'refresh'));
    container.append(actions);
  } else {
    container.append(element('h2', '', snapshot.repository ? 'The next quest starts here.' : 'Good work deserves a helping hand.'));
    container.append(element('p', '', snapshot.repository ? 'The repository is connected. Ask your agent to prepare the first task, then approve it for publication.' : 'This board is not connected yet. Ask the maintainer to configure GitHub Actions and Pages, or explore the demo.'));
    const actions = element('div', 'empty-actions');
    actions.append(button(snapshot.repository ? 'Publish with your agent' : 'How it works', 'button primary', snapshot.repository ? openPublish : () => openDialog($('#guide-dialog')), snapshot.repository ? 'plus' : 'book'));
    actions.append(button('Explore the demo', 'button secondary', () => loadSnapshot('demo'), 'play'));
    container.append(actions);
    const steps = element('div', 'onboarding-steps');
    for (const [index, label] of ['Agent prepares', 'Approve publication', 'Download and run', 'Review results'].entries()) {
      if (index) steps.append(icon('arrow', true));
      const step = element('span');
      step.append(element('b', '', String(index + 1)), document.createTextNode(label));
      steps.append(step);
    }
    container.append(steps);
  }
  return container;
}
function renderTasks() {
  const tasks = filterTasks(snapshot.tasks, filters).sort((a, b) => {
    const time = item => typeof item.updated_at === 'number' ? item.updated_at * 1000 : Date.parse(item.updated_at || item.created_at) || 0;
    return time(b) - time(a) || b.number - a.number;
  });
  const list = $('#task-list');
  list.setAttribute('aria-busy', 'false');
  list.replaceChildren(...(tasks.length ? tasks.map(renderTask) : [renderEmpty()]));
  $('#result-count').textContent = `${snapshot.tasks.length} ${snapshot.tasks.length === 1 ? 'task' : 'tasks'}${tasks.length !== snapshot.tasks.length ? ` · Showing ${tasks.length}` : ''}`;
}
function renderError(error) {
  for (const name of ['total', 'open', 'active', 'submitted']) $(`#count-${name}`).textContent = '—';
  for (const node of $$('[data-tab-count]')) node.textContent = '—';
  $('#nav-count').textContent = '—';
  $('#task-list').setAttribute('aria-busy', 'false');
  const panel = element('div', 'empty-state error');
  panel.setAttribute('role', 'alert');
  panel.append(emptyArt('info'), element('h2', '', 'Tasks are unavailable'), element('p', '', 'Please retry later, or ask the maintainer to check the deployment. Existing tasks remain available on GitHub.'));
  const actions = element('div', 'empty-actions');
  actions.append(button('Retry', 'button primary', () => loadSnapshot(currentMode), 'refresh'), button('Explore demo', 'button secondary', () => loadSnapshot('demo'), 'play'));
  panel.append(actions, element('span', 'empty-footnote', error.message));
  $('#task-list').replaceChildren(panel);
  $('#result-count').textContent = 'Unable to load';
  $('#sync-status').textContent = 'Task data is unavailable';
  renderLeaderboards('error');
}
async function loadSnapshot(mode = currentMode) {
  currentMode = mode;
  const version = ++loadVersion;
  loading = true;
  lastError = null;
  $('#refresh-button').disabled = true;
  $('#task-list').setAttribute('aria-busy', 'true');
  const state = element('div', 'loading-state');
  state.setAttribute('role', 'status');
  state.append(element('span', 'loader'), document.createTextNode('Loading tasks from GitHub…'));
  $('#task-list').replaceChildren(state);
  $('#result-count').textContent = 'Loading tasks…';
  $('#sync-status').textContent = 'Loading task data';
  renderLeaderboards('loading');
  try {
    const response = await fetch(mode === 'demo' ? './demo-tasks.json' : './tasks.json', { cache: 'no-store', signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`Task data returned HTTP ${response.status}`);
    const raw = await response.text();
    if (raw.length > 5 * 1024 * 1024) throw new Error('Task data exceeds the 5 MB limit.');
    const data = normalizeSnapshot(JSON.parse(raw));
    if (mode === 'demo' && !data.demo) throw new Error('Demo data is missing its demo flag.');
    if (mode === 'live' && data.demo) throw new Error('The live board cannot contain demo data.');
    if (version !== loadVersion) return;
    snapshot = data;
    resetFilters(false);
    renderWorkspace();
    renderCounts();
    renderTasks();
    renderLeaderboards();
    const url = new URL(location.href);
    if (mode === 'demo') url.searchParams.set('demo', '1'); else url.searchParams.delete('demo');
    history.replaceState({}, '', url);
  } catch (error) {
    if (version !== loadVersion) return;
    lastError = error;
    snapshot = { ...emptySnapshot };
    renderWorkspace();
    renderCounts();
    renderError(error);
  } finally {
    if (version === loadVersion) {
      loading = false;
      $('#refresh-button').disabled = false;
      renderPublisher();
    }
  }
}
function resetFilters(render = true) {
  filters = { query: '', status: 'all', agent: 'all', category: 'all' };
  $('#search-input').value = '';
  $('#agent-filter').value = 'all';
  $('#category-filter').value = 'all';
  for (const tab of $$('[data-status]')) {
    tab.classList.toggle('selected', tab.dataset.status === 'all');
    tab.setAttribute('aria-pressed', String(tab.dataset.status === 'all'));
  }
  if (render && !loading && !lastError) renderTasks();
}

function section(title, content, extra) {
  const wrapper = element('section', 'detail-section');
  const heading = element('div', 'detail-section-heading');
  heading.append(element('h3', '', title));
  if (extra) heading.append(extra);
  wrapper.append(heading);
  if (content) wrapper.append(content);
  return wrapper;
}
function paragraph(text, className = 'detail-description') { return element('p', className, text); }
function outputChips(values) {
  const list = element('ul', 'output-list');
  for (const value of values || []) {
    const item = element('li');
    item.append(icon('file', true), element('code', '', value));
    list.append(item);
  }
  return list;
}
function renderResources(resources) {
  if (!resources?.length) return paragraph('No additional resources are attached to this task.', 'detail-empty');
  const list = element('ul', 'resource-list');
  for (const resource of resources) {
    const item = element('li');
    const title = element('div', 'resource-title');
    const url = resourceURL(resource, snapshot.hostname);
    title.append(icon('file', true), url && !snapshot.demo ? link(resource.path, url) : element('span', '', resource.path));
    item.append(title, paragraph(`Pinned commit ${String(resource.commit || '').slice(0, 12)} · Saved to ${resource.destination || 'Not specified'}`, 'resource-meta'));
    item.append(paragraph(`SHA-256 ${resource.sha256 || 'Not specified'}`, 'resource-meta'));
    list.append(item);
  }
  return list;
}
function renderHandoff(handoff) {
  const content = element('div', 'handoff-notes');
  function notes(label, values, empty = 'None declared.') {
    content.append(paragraph(label, 'small-label'));
    if (!Array.isArray(values) || !values.length) {
      content.append(paragraph(empty, 'detail-empty'));
      return;
    }
    const list = element('ul', 'detail-description');
    for (const value of values) list.append(element('li', '', value));
    content.append(list);
  }
  notes('Out of scope', handoff.non_goals);
  notes('Constraints and established decisions', handoff.constraints);
  notes('Assumptions', handoff.assumptions);
  content.append(paragraph('Environment and access', 'small-label'), paragraph(handoff.environment || 'Not specified'));
  notes('When to stop', handoff.stop_conditions);
  content.append(paragraph('Author’s closure review', 'small-label'), paragraph('The author’s declared starting point, inputs, and completion plan.'));
  for (const [key, label] of [['first_step', 'First action'], ['inputs', 'Required inputs and their purpose'], ['completion', 'Completion and delivery']]) {
    content.append(paragraph(label, 'small-label'), paragraph(handoff.review?.[key] || 'Not specified'));
  }
  notes('Blocking questions', handoff.review?.blocking_questions, 'No blocking questions declared.');
  return content;
}
function renderResult(result) {
  if (!result?.manifest) return paragraph('No result has been submitted yet. The summary, verification records, and files will appear here after execution.', 'detail-empty');
  const manifest = result.manifest;
  const card = element('div', 'result-card');
  card.append(paragraph(manifest.summary || 'No result summary available'));
  for (const artifact of manifest.artifacts || []) {
    const url = safeHTTPS(artifact.uri);
    if (url && !snapshot.demo) card.append(link(artifact.name, url, 'artifact-link', 'arrow-up'));
    else {
      const item = element('div', 'artifact-link');
      item.append(icon('file', true), document.createTextNode(`${artifact.name}${snapshot.demo ? ' · Demo file' : ' · Link unavailable'}`));
      card.append(item);
    }
  }
  if (manifest.verification?.length) {
    card.append(paragraph('Verification records', 'small-label'));
    for (const check of manifest.verification) {
      const row = element('div', `verification-row${check.exit_code === 0 ? '' : ' failed'}`);
      row.append(icon(check.exit_code === 0 ? 'circle-check' : 'info', true), element('code', '', `${JSON.stringify(check.argv)} · exit ${check.exit_code}`));
      card.append(row);
      if (check.evidence) card.append(paragraph(check.evidence, 'resource-meta'));
    }
  }
  for (const [key, label] of [['assumptions', 'Assumptions'], ['unresolved', 'Unresolved items']]) {
    if (manifest[key]?.length) {
      card.append(paragraph(label, 'small-label'));
      for (const item of manifest[key]) card.append(paragraph(`• ${item}`));
    }
  }
  return card;
}
function showTask(task) {
  const spec = task.spec;
  const header = element('div', 'dialog-header');
  const heading = element('div');
  const headerMeta = element('div', 'detail-header-meta');
  headerMeta.append(element('span', '', `${snapshot.demo ? 'Demo task' : 'Task'} #${task.number}`), statusBadge(task.status));
  if (snapshot.demo) headerMeta.append(element('span', 'demo-badge', 'Fictional data'));
  const title = element('h2', '', spec.title);
  title.id = 'detail-title';
  const subtitle = element('div', 'detail-subtitle');
  subtitle.append(element('span', 'author-avatar', String(task.author || 'A').slice(0, 2).toUpperCase()), element('span', '', `Published by ${task.author || 'Unknown author'}`), element('span', 'meta-dot', '·'), element('span', '', `Revision ${spec.revision || 1}`), element('span', 'meta-dot', '·'), element('span', '', relativeTime(task.updated_at || task.created_at)));
  heading.append(headerMeta, title, subtitle);
  const close = button('', 'icon-button', () => closeDialog($('#detail-dialog')), 'close');
  close.setAttribute('aria-label', 'Close task details');
  header.append(heading, close);
  const body = element('div', 'dialog-body detail-layout');
  const main = element('div');
  main.append(section('Task prompt', paragraph(spec.prompt, 'prompt-text'), button('Copy prompt', 'text-button', () => copyText(taskExecutionPrompt(spec), 'Full prompt and task context copied'), 'copy')));
  if (spec.delegation_reason) main.append(section('Why delegate', paragraph(spec.delegation_reason)));
  main.append(section('Resources', renderResources(spec.resources)));
  if (spec.handoff) main.append(section('Handoff notes', renderHandoff(spec.handoff)));
  const acceptance = element('div');
  acceptance.append(paragraph('Verification commands', 'small-label'));
  if (spec.acceptance?.commands?.length) {
    for (const command of spec.acceptance.commands) acceptance.append(element('pre', 'command-block', JSON.stringify(command)));
  } else acceptance.append(paragraph('No automated acceptance checks are specified.', 'detail-empty'));
  acceptance.append(paragraph('Required outputs', 'small-label'), outputChips(spec.acceptance?.required_outputs));
  if (spec.acceptance?.review_notes) acceptance.append(paragraph('Review notes', 'small-label'), paragraph(spec.acceptance.review_notes));
  main.append(section('Acceptance criteria', acceptance), section('Result', renderResult(task.result)));
  const side = element('aside', 'detail-side');
  function sideItem(label, value) {
    const block = element('div');
    block.append(paragraph(label, 'detail-side-label'));
    const content = element('div', 'detail-side-value');
    if (value instanceof Node) content.append(value); else content.textContent = String(value);
    block.append(content);
    side.append(block);
  }
  const agents = element('div');
  spec.execution.compatible_agents.forEach(agent => agents.append(agentChip(agent)));
  sideItem('Compatible agents', agents);
  sideItem('Category / Size', `${CATEGORIES[spec.category] || 'Code'} / ${spec.size || 'M'}`);
  sideItem('Time limit', executionDuration(spec.execution.timeout_seconds));
  sideItem('Attempts', `${task.attempt_count || 0} / ${spec.execution.max_attempts || 1}`);
  const source = safeHTTPS(spec.source?.repository);
  sideItem('Source repository', source && !snapshot.demo && new URL(source).hostname === snapshot.hostname ? link(source.replace(`https://${snapshot.hostname}/`, ''), source, '', 'arrow-up') : String(spec.source?.repository || 'Not specified').replace(/^https?:\/\//, ''));
  sideItem('Base commit', element('code', '', spec.source?.base_commit || 'Not specified'));
  sideItem('Allowed changes', outputChips(spec.source?.write_paths));
  if (task.attempt?.actor) sideItem('Current executor', task.attempt.actor);
  if (task.attempt?.expires_at) sideItem('Claim expires', new Date(task.attempt.expires_at * 1000).toLocaleString('en-US', { hour12: false }));
  body.append(main, side);
  const advanced = element('details', 'detail-advanced');
  advanced.append(element('summary', '', 'Advanced · Copy a command for an existing CLI setup'));
  const copyGroup = element('div', 'detail-footer-copy');
  const agentSelect = element('select');
  agentSelect.setAttribute('aria-label', 'Select a local agent');
  for (const agent of spec.execution.compatible_agents) {
    const option = element('option', '', agent === 'codex' ? 'Codex' : 'Claude Code');
    option.value = agent;
    agentSelect.append(option);
  }
  const copyCommand = button('Copy CLI command', 'button secondary', () => copyText(taskCommand(snapshot, task.number, agentSelect.value), 'Run command copied'), 'terminal');
  copyCommand.disabled = snapshot.demo;
  if (snapshot.demo) copyCommand.title = 'Demo tasks cannot be run';
  copyGroup.append(agentSelect, copyCommand);
  advanced.append(copyGroup);
  main.append(advanced);
  const footer = element('div', 'dialog-footer');
  if (!snapshot.demo) footer.append(link('GitHub', githubURL(snapshot, `issues/${task.number}`), 'button secondary', 'arrow-up'));
  const run = button(snapshot.demo ? 'Demo only' : task.status === 'open' ? 'Claim task' : 'Not available to claim', 'button primary', () => openDownload({ task }), 'download');
  run.disabled = snapshot.demo || task.status !== 'open';
  if (snapshot.demo) run.title = 'Downloads and execution are disabled for demo tasks';
  footer.append(run);
  const note = paragraph(snapshot.demo ? 'This is a fictional example. Downloads and execution are disabled.' : 'Choose your OS → Download and extract → Double-click the starter → Follow setup → Choose an agent. Execution starts after GitHub confirms your claim.', 'detail-footer-note');
  $('#detail-content').replaceChildren(header, body, footer, note);
  openDialog($('#detail-dialog'));
}

function renderPublisher() {
  const disabled = loading || lastError || snapshot.demo || !snapshot.repository;
  $('#copy-publisher-instructions').disabled = Boolean(disabled);
  $('#publisher-setup').disabled = Boolean(disabled);
  $('#publisher-unavailable').hidden = !disabled;
  $('#publisher-unavailable').textContent = snapshot.demo ? 'Publisher setup is disabled in the demo. Return to the live board to copy instructions or download setup.' : loading ? 'Loading the repository. Publisher setup will be available shortly.' : 'The repository is not connected or task data is unavailable. Ask the maintainer to check setup before connecting your agent.';
  $('#publisher-instructions').value = disabled ? '' : publisherInstructions(snapshot, document.baseURI);
}

function openDownload({ task, action = 'run' } = {}) {
  if (loading || lastError || snapshot.demo || !snapshot.repository) {
    showToast('Open a connected live board first.', true);
    return;
  }
  const board = snapshot;
  const dialog = $('#download-dialog');
  let selectedPlatform = '';
  let busy = false;
  let active = true;
  dialog.addEventListener('close', () => { active = false; }, { once: true });
  const header = element('div', 'dialog-header');
  const heading = element('div');
  heading.append(element('span', 'eyebrow', action === 'run' ? `QUEST #${task.number} / READY TO START` : 'PUBLISHER SETUP / CONNECT YOUR AGENT'));
  const title = element('h2', '', action === 'run' ? 'Choose your operating system' : 'Connect your existing agent');
  title.id = 'download-title';
  heading.append(title, paragraph(action === 'run' ? 'Download the package, follow setup, and choose a compatible agent to run the task.' : 'Choose your project and agent during setup. Your agent publishes only after you approve a proposal.'));
  const close = button('', 'icon-button', () => dialog.close(), 'close');
  close.setAttribute('aria-label', 'Close download');
  header.append(heading, close);
  const body = element('div', 'dialog-body download-body');
  const choices = element('fieldset', 'platform-choices');
  choices.append(element('legend', '', 'Which operating system do you use?'));
  const platformNote = paragraph('Choose an OS. Each package includes first-run setup, with no initialization commands to type.', 'download-platform-note');
  const error = paragraph('', 'form-error');
  error.hidden = true;
  error.setAttribute('role', 'alert');
  const progress = paragraph('', 'download-progress');
  progress.hidden = true;
  progress.setAttribute('role', 'status');
  const success = element('div', 'download-success');
  success.hidden = true;
  success.setAttribute('role', 'status');
  const footer = element('div', 'dialog-footer');
  const download = button('Select an OS to download', 'button primary', async () => {
    if (busy || !selectedPlatform) return;
    if (board !== snapshot || loading || lastError) {
      error.textContent = 'Task data has changed. Close this window and select the task again.';
      error.hidden = false;
      return;
    }
    busy = true;
    error.hidden = true;
    success.hidden = true;
    progress.textContent = 'Verifying the runtime and preparing your download…';
    progress.hidden = false;
    download.disabled = true;
    for (const input of choices.querySelectorAll('input')) input.disabled = true;
    try {
      const result = await createDownloadPackage({ snapshot: board, task, platform: selectedPlatform, action }, { pageURL: document.baseURI });
      if (!active) return;
      if (board !== snapshot || loading || lastError) throw new Error('Task data has changed. Close this window and download again.');
      saveDownload(new Blob([result.bytes], { type: 'application/zip' }), result.filename);
      success.replaceChildren(element('strong', '', 'Your package is ready'), paragraph(`Find ${result.filename} in your browser downloads. Extract all files, then double-click ${DOWNLOAD_PLATFORMS[selectedPlatform].starter}. ${action === 'run' ? 'Downloading does not claim the task. The starter checks its live status again.' : 'Follow the setup guide to connect the publishing skill to your project.'}`));
      success.hidden = false;
    } catch (issue) {
      if (!active) return;
      error.textContent = issue.message || 'Download failed. Check your connection and retry.';
      error.hidden = false;
    } finally {
      busy = false;
      if (active) {
        progress.hidden = true;
        download.disabled = false;
        for (const input of choices.querySelectorAll('input')) input.disabled = false;
      }
    }
  }, 'download');
  download.id = 'package-download';
  download.disabled = true;
  for (const [value, platform] of Object.entries(DOWNLOAD_PLATFORMS)) {
    const option = element('label', 'platform-option');
    const radio = element('input');
    radio.type = 'radio';
    radio.name = 'download-platform';
    radio.value = value;
    radio.addEventListener('change', () => {
      selectedPlatform = value;
      platformNote.textContent = platform.note;
      download.replaceChildren(icon('download'), document.createTextNode(`Download for ${platform.label}`));
      download.disabled = busy;
      success.hidden = true;
      error.hidden = true;
    });
    option.append(radio, element('strong', '', platform.label), element('span', '', platform.starter));
    choices.append(option);
  }
  const steps = element('ol', 'download-steps');
  for (const [headingText, description] of [
    ['Extract all files', 'Extract every file in the ZIP into the same folder.'],
    ['Double-click to start', 'Open the Start-Taskboard file for your operating system.'],
    ['Follow first-run setup', 'Follow the official installation guidance for missing tools, then sign in to GitHub and your agent.'],
    ['Choose an agent', action === 'run' ? 'Confirm and run the task. Results are submitted automatically and the publisher is notified to review them.' : 'Select your project and agent, then prepare and approve a proposal in your original session.'],
  ]) {
    const item = element('li');
    item.append(element('strong', '', headingText), paragraph(description));
    steps.append(item);
  }
  body.append(choices, platformNote, steps, paragraph('Requires Python, Git, GitHub CLI, and your chosen agent. Setup checks these tools and offers installation help. This package contains unsigned scripts. Verify their source according to your device and organization policies.', 'download-trust-note'), progress, error, success);
  footer.append(button('Back', 'button secondary', () => dialog.close()), download);
  $('#download-content').replaceChildren(header, body, footer);
  openDialog(dialog);
}

function openPublish() {
  const form = $('#publish-form');
  if (exportedTask) {
    form.reset();
    taskId = undefined;
    exportedTask = false;
    $('#export-success').hidden = true;
    $('#form-error').hidden = true;
  }
  if (!taskId) taskId = crypto.randomUUID();
  if (!form.elements.repository.value && snapshot.repository && !snapshot.demo) form.elements.repository.value = githubURL(snapshot);
  form.elements.repository.placeholder = `https://${snapshot.hostname}/owner/repo`;
  $('#source-host-help').textContent = `Use an HTTPS repository URL on ${snapshot.hostname}, without a .git suffix.`;
  renderPublisher();
  openDialog($('#publish-dialog'));
}
function exportTask(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const error = $('#form-error');
  error.hidden = true;
  $('#export-success').hidden = true;
  const values = Object.fromEntries(new FormData(form));
  values.agents = new FormData(form).getAll('agents');
  try {
    const task = buildTaskExport(values, { hostname: snapshot.hostname, taskId });
    const filename = `task-${task.task_id.slice(0, 8)}.json`;
    const blob = new Blob([`${JSON.stringify(task, null, 2)}\n`], { type: 'application/json;charset=utf-8' });
    saveDownload(blob, filename);
    const base = snapshot.repository && !snapshot.demo ? `taskboard --repo ${snapshot.repository} --hostname ${snapshot.hostname}` : `taskboard --repo owner/repo --hostname ${snapshot.hostname}`;
    $('#publish-command').textContent = `${base} publish ${filename}`;
    $('#export-success').hidden = false;
    exportedTask = true;
    $('#export-success').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    showToast('Task JSON exported. Publish it with your local CLI.');
  } catch (issue) {
    error.textContent = issue.message;
    error.hidden = false;
    error.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

for (const dialog of $$('dialog')) {
  dialog.addEventListener('close', () => {
    if (dialog.contains($('#toast'))) document.body.append($('#toast'));
  });
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDialog(dialog);
  });
}
for (const trigger of $$('[data-close-dialog]')) trigger.addEventListener('click', () => closeDialog(trigger.closest('dialog')));
for (const trigger of $$('[data-open-guide]')) trigger.addEventListener('click', () => openDialog($('#guide-dialog')));
for (const trigger of $$('[data-copy]')) trigger.addEventListener('click', () => copyText(trigger.dataset.copy));
$('#copy-publisher-instructions').addEventListener('click', () => copyText($('#publisher-instructions').value, 'Copied. Paste this into your current agent session.'));
$('#publisher-setup').addEventListener('click', () => openDownload({ action: 'install-publisher' }));
$('#copy-publish-command').addEventListener('click', () => copyText($('#publish-command').textContent));
$('#publish-button').addEventListener('click', openPublish);
$('#publish-form').addEventListener('submit', exportTask);
$('#leave-demo').addEventListener('click', () => loadSnapshot('live'));
$('#try-demo-footer').addEventListener('click', () => loadSnapshot(currentMode === 'demo' ? 'live' : 'demo'));
$('#refresh-button').addEventListener('click', () => loadSnapshot(currentMode));
$('#nav-board').addEventListener('click', () => { resetFilters(); $('#main').scrollIntoView({ behavior: 'smooth' }); });
$('#search-input').addEventListener('input', event => { filters.query = event.target.value; if (!loading && !lastError) renderTasks(); });
$('#agent-filter').addEventListener('change', event => { filters.agent = event.target.value; if (!loading && !lastError) renderTasks(); });
$('#category-filter').addEventListener('change', event => { filters.category = event.target.value; if (!loading && !lastError) renderTasks(); });
for (const tab of $$('[data-status]')) tab.addEventListener('click', () => {
  filters.status = tab.dataset.status;
  for (const item of $$('[data-status]')) {
    item.classList.toggle('selected', item === tab);
    item.setAttribute('aria-pressed', String(item === tab));
  }
  if (!loading && !lastError) renderTasks();
});
document.addEventListener('keydown', event => {
  if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !document.querySelector('dialog[open]') && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) {
    event.preventDefault();
    $('#search-input').focus();
  }
});
loadSnapshot();
