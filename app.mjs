import { STATUS, CATEGORIES, normalizeSnapshot, filterTasks, taskCounts, taskLeaderboards, executionDuration, githubURL, taskCommand, safeHTTPS, resourceURL, buildTaskExport, relativeTime } from './model.mjs';
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
async function copyText(text, success = '已复制到剪贴板') {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('当前浏览器不支持剪贴板');
    await navigator.clipboard.writeText(text);
    showToast(success);
  } catch {
    const dialog = element('dialog', 'dialog copy-dialog');
    dialog.setAttribute('aria-labelledby', 'manual-copy-title');
    const header = element('div', 'dialog-header');
    const title = element('h2', '', '手动复制');
    title.id = 'manual-copy-title';
    const close = button('', 'icon-button', () => dialog.close(), 'close');
    close.setAttribute('aria-label', '关闭手动复制');
    header.append(title, close);
    const body = element('div', 'dialog-body');
    const field = element('label', 'field', '浏览器未允许自动复制，请选中以下内容复制。');
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
  $('#ranking-scope').textContent = state === 'loading' ? '正在读取已确认的任务记录…' : state === 'error' ? '快照暂不可用' : snapshot.demo ? '演示任务不计入真实排行' : '当前确认快照 · 各显示前 10 名 · 不受列表筛选影响';
  for (const [kind, label] of [['published', '发布'], ['completed', '完成']]) {
    const list = $(`#leaderboard-${kind}`);
    const entries = state ? [] : rankings[kind];
    if (!entries.length) {
      list.replaceChildren(element('li', 'ranking-empty', state === 'loading' ? '正在读取…' : state === 'error' ? '暂时无法确认排行，请稍后刷新。' : snapshot.demo ? '返回真实看板后查看公会贡献。' : kind === 'published' ? '还没有已确认的发布记录。' : '还没有已验收的完成记录。'));
      continue;
    }
    list.replaceChildren(...entries.map((entry, index) => {
      const row = element('li', 'ranking-row');
      const position = element('span', 'ranking-position', String(index + 1).padStart(2, '0'));
      position.setAttribute('aria-label', `第 ${index + 1} 名`);
      row.append(position, element('span', 'ranking-actor', `@${entry.actor}`), element('strong', 'ranking-count', `${entry.count} 项`));
      row.setAttribute('aria-label', `第 ${index + 1} 名，${entry.actor}，${label} ${entry.count} 项`);
      return row;
    }));
  }
}
function renderWorkspace() {
  $('#demo-banner').hidden = !snapshot.demo;
  $('#workspace-repo').textContent = snapshot.demo ? '演示工作空间 · 虚构数据' : snapshot.repository || '等待连接仓库';
  $('#workspace-repo').title = snapshot.repository || '尚未配置 GitHub 任务仓库';
  const repoURL = snapshot.repository && !snapshot.demo ? githubURL(snapshot) : null;
  setExternalLink($('#nav-repository'), repoURL);
  const docs = $('#guide-docs-link');
  docs.hidden = !repoURL;
  if (repoURL) setExternalLink(docs, repoURL);
  $('#try-demo-footer').textContent = snapshot.demo ? '返回真实工作空间' : '体验演示工作空间';
  $('#try-demo-footer').append(icon('arrow', true));
  $('#sync-status').textContent = snapshot.demo ? '演示快照 · 非真实执行记录' : snapshot.repository ? `快照同步于 ${relativeTime(snapshot.generated_at)}` : '尚未连接任务仓库';
  $('#sync-status').title = snapshot.generated_at || '暂无同步记录';
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
  rank.setAttribute('aria-label', `任务规模 ${size}`);
  rank.title = `星级对应任务规模 ${size}`;
  docket.append(element('span', 'quest-number', `委托书 / No. ${String(task.number).padStart(3, '0')}`), rank);
  const categoryIcon = element('span', `task-category-icon ${category}`);
  categoryIcon.append(icon(category === 'docs' ? 'file' : category === 'research' ? 'research' : 'code'));
  const content = element('div', 'task-content');
  const titleLine = element('div', 'task-title-line');
  const title = button(spec.title, 'task-title', () => showTask(task));
  title.setAttribute('aria-label', `查看任务 #${task.number}：${spec.title}`);
  titleLine.append(title);
  if (spec.priority === 'high') titleLine.append(element('span', 'priority-chip', '优先处理'));
  const description = element('p', 'task-description', spec.prompt.replace(/^【虚构演示任务】\s*/, ''));
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
    const view = button('查看任务', 'button ghost compact', () => showTask(task), 'arrow');
    actions.append(view);
  } else {
    const view = button(task.status === 'submitted' || task.status === 'accepted' ? '查看交付' : '查看详情', 'row-link', () => showTask(task));
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
    container.append(element('h2', '', '还没有符合条件的任务'), element('p', '', '换个关键词，或调整筛选条件，看看其他正在等待接力的任务。'));
    const actions = element('div', 'empty-actions');
    actions.append(button('清除全部筛选', 'button secondary', resetFilters, 'refresh'));
    container.append(actions);
  } else {
    container.append(element('h2', '', snapshot.repository ? '下一次接力，从这里开始。' : '好任务，值得一次好接力。'));
    container.append(element('p', '', snapshot.repository ? '仓库已连接，当前还没有任务。让现有 Agent 整理上下文，在你确认后发布第一份委托。' : '当前看板尚未连接任务仓库。请联系维护者完成 GitHub Actions 与 Pages 配置；也可以先浏览演示。'));
    const actions = element('div', 'empty-actions');
    actions.append(button(snapshot.repository ? '让 Agent 发布委托' : '了解使用方式', 'button primary', snapshot.repository ? openPublish : () => openDialog($('#guide-dialog')), snapshot.repository ? 'plus' : 'book'));
    actions.append(button('先体验演示', 'button secondary', () => loadSnapshot('demo'), 'play'));
    container.append(actions);
    const steps = element('div', 'onboarding-steps');
    for (const [index, label] of ['Agent 整理', '确认发布', '下载执行', '检查交付'].entries()) {
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
  $('#result-count').textContent = `共 ${snapshot.tasks.length} 项任务${tasks.length !== snapshot.tasks.length ? ` · 显示 ${tasks.length} 项` : ''}`;
}
function renderError(error) {
  for (const name of ['total', 'open', 'active', 'submitted']) $(`#count-${name}`).textContent = '—';
  for (const node of $$('[data-tab-count]')) node.textContent = '—';
  $('#nav-count').textContent = '—';
  $('#task-list').setAttribute('aria-busy', 'false');
  const panel = element('div', 'empty-state error');
  panel.setAttribute('role', 'alert');
  panel.append(emptyArt('info'), element('h2', '', '暂时无法读取任务'), element('p', '', '请检查任务快照是否已部署，或稍后重试。现有 GitHub 任务不会受到影响。'));
  const actions = element('div', 'empty-actions');
  actions.append(button('重新读取', 'button primary', () => loadSnapshot(currentMode), 'refresh'), button('浏览演示', 'button secondary', () => loadSnapshot('demo'), 'play'));
  panel.append(actions, element('span', 'empty-footnote', error.message));
  $('#task-list').replaceChildren(panel);
  $('#result-count').textContent = '读取失败';
  $('#sync-status').textContent = '任务快照暂不可用';
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
  state.append(element('span', 'loader'), document.createTextNode('正在读取 GitHub 任务快照…'));
  $('#task-list').replaceChildren(state);
  $('#result-count').textContent = '正在读取任务…';
  $('#sync-status').textContent = '读取快照中';
  renderLeaderboards('loading');
  try {
    const response = await fetch(mode === 'demo' ? './demo-tasks.json' : './tasks.json', { cache: 'no-store', signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`任务快照返回 HTTP ${response.status}`);
    const raw = await response.text();
    if (raw.length > 5 * 1024 * 1024) throw new Error('快照文件超过 5 MB，无法读取。');
    const data = normalizeSnapshot(JSON.parse(raw));
    if (mode === 'demo' && !data.demo) throw new Error('演示快照缺少 demo 标记。');
    if (mode === 'live' && data.demo) throw new Error('真实任务快照不能包含演示数据。');
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
  if (!resources?.length) return paragraph('此任务没有附加参考资源。', 'detail-empty');
  const list = element('ul', 'resource-list');
  for (const resource of resources) {
    const item = element('li');
    const title = element('div', 'resource-title');
    const url = resourceURL(resource, snapshot.hostname);
    title.append(icon('file', true), url && !snapshot.demo ? link(resource.path, url) : element('span', '', resource.path));
    item.append(title, paragraph(`固定提交 ${String(resource.commit || '').slice(0, 12)} · 保存至 ${resource.destination || '未指定'}`, 'resource-meta'));
    item.append(paragraph(`SHA-256 ${resource.sha256 || '未指定'}`, 'resource-meta'));
    list.append(item);
  }
  return list;
}
function renderResult(result) {
  if (!result?.manifest) return paragraph('结果尚未提交。执行完成后，这里会显示摘要、验证记录与交付文件。', 'detail-empty');
  const manifest = result.manifest;
  const card = element('div', 'result-card');
  card.append(paragraph(manifest.summary || '暂无结果摘要'));
  for (const artifact of manifest.artifacts || []) {
    const url = safeHTTPS(artifact.uri);
    if (url && !snapshot.demo) card.append(link(artifact.name, url, 'artifact-link', 'arrow-up'));
    else {
      const item = element('div', 'artifact-link');
      item.append(icon('file', true), document.createTextNode(`${artifact.name}${snapshot.demo ? ' · 演示文件' : ' · 链接不可用'}`));
      card.append(item);
    }
  }
  if (manifest.verification?.length) {
    card.append(paragraph('验证记录', 'small-label'));
    for (const check of manifest.verification) {
      const row = element('div', `verification-row${check.exit_code === 0 ? '' : ' failed'}`);
      row.append(icon(check.exit_code === 0 ? 'circle-check' : 'info', true), element('code', '', `${JSON.stringify(check.argv)} · exit ${check.exit_code}`));
      card.append(row);
      if (check.evidence) card.append(paragraph(check.evidence, 'resource-meta'));
    }
  }
  for (const [key, label] of [['assumptions', '执行假设'], ['unresolved', '未解决事项']]) {
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
  headerMeta.append(element('span', '', `${snapshot.demo ? '演示任务' : '任务'} #${task.number}`), statusBadge(task.status));
  if (snapshot.demo) headerMeta.append(element('span', 'demo-badge', '虚构数据'));
  const title = element('h2', '', spec.title);
  title.id = 'detail-title';
  const subtitle = element('div', 'detail-subtitle');
  subtitle.append(element('span', 'author-avatar', String(task.author || 'A').slice(0, 2).toUpperCase()), element('span', '', `${task.author || '未知作者'} 发布`), element('span', 'meta-dot', '·'), element('span', '', `修订 ${spec.revision || 1}`), element('span', 'meta-dot', '·'), element('span', '', relativeTime(task.updated_at || task.created_at)));
  heading.append(headerMeta, title, subtitle);
  const close = button('', 'icon-button', () => closeDialog($('#detail-dialog')), 'close');
  close.setAttribute('aria-label', '关闭任务详情');
  header.append(heading, close);
  const body = element('div', 'dialog-body detail-layout');
  const main = element('div');
  main.append(section('任务说明', paragraph(spec.prompt, 'prompt-text'), button('复制提示词', 'text-button', () => copyText(spec.prompt, '提示词已复制'), 'copy')));
  if (spec.delegation_reason) main.append(section('委派原因', paragraph(spec.delegation_reason)));
  main.append(section('参考资源', renderResources(spec.resources)));
  const acceptance = element('div');
  acceptance.append(paragraph('验收命令', 'small-label'));
  if (spec.acceptance?.commands?.length) {
    for (const command of spec.acceptance.commands) acceptance.append(element('pre', 'command-block', JSON.stringify(command)));
  } else acceptance.append(paragraph('未指定自动验收命令。', 'detail-empty'));
  acceptance.append(paragraph('必需交付', 'small-label'), outputChips(spec.acceptance?.required_outputs));
  if (spec.acceptance?.review_notes) acceptance.append(paragraph('验收备注', 'small-label'), paragraph(spec.acceptance.review_notes));
  main.append(section('验收标准', acceptance), section('执行结果', renderResult(task.result)));
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
  sideItem('兼容 Agent', agents);
  sideItem('任务类型 / 规模', `${CATEGORIES[spec.category] || '代码'} / ${spec.size || 'M'}`);
  sideItem('执行时限', executionDuration(spec.execution.timeout_seconds));
  sideItem('尝试次数', `${task.attempt_count || 0} / ${spec.execution.max_attempts || 1}`);
  const source = safeHTTPS(spec.source?.repository);
  sideItem('源代码仓库', source && !snapshot.demo && new URL(source).hostname === snapshot.hostname ? link(source.replace(`https://${snapshot.hostname}/`, ''), source, '', 'arrow-up') : String(spec.source?.repository || '未指定').replace(/^https?:\/\//, ''));
  sideItem('基准提交', element('code', '', spec.source?.base_commit || '未指定'));
  sideItem('允许修改', outputChips(spec.source?.write_paths));
  if (task.attempt?.actor) sideItem('当前执行者', task.attempt.actor);
  if (task.attempt?.expires_at) sideItem('领取有效期至', new Date(task.attempt.expires_at * 1000).toLocaleString('zh-CN', { hour12: false }));
  body.append(main, side);
  const advanced = element('details', 'detail-advanced');
  advanced.append(element('summary', '', '高级用法 · 已配置 CLI？复制运行命令'));
  const copyGroup = element('div', 'detail-footer-copy');
  const agentSelect = element('select');
  agentSelect.setAttribute('aria-label', '选择本地执行 Agent');
  for (const agent of spec.execution.compatible_agents) {
    const option = element('option', '', agent === 'codex' ? 'Codex' : 'Claude Code');
    option.value = agent;
    agentSelect.append(option);
  }
  const copyCommand = button('复制 CLI', 'button secondary', () => copyText(taskCommand(snapshot, task.number, agentSelect.value), '运行命令已复制'), 'terminal');
  copyCommand.disabled = snapshot.demo;
  if (snapshot.demo) copyCommand.title = '演示任务不可执行';
  copyGroup.append(agentSelect, copyCommand);
  advanced.append(copyGroup);
  main.append(advanced);
  const footer = element('div', 'dialog-footer');
  if (!snapshot.demo) footer.append(link('GitHub', githubURL(snapshot, `issues/${task.number}`), 'button secondary', 'arrow-up'));
  const run = button(snapshot.demo ? '演示任务不可领取' : task.status === 'open' ? '领取任务' : '当前不可领取', 'button primary', () => openDownload({ task }), 'download');
  run.disabled = snapshot.demo || task.status !== 'open';
  if (snapshot.demo) run.title = '演示任务不能下载或执行';
  footer.append(run);
  const note = paragraph(snapshot.demo ? '此任务仅用于展示界面，下载与执行均已禁用。' : '选择系统并下载运行包 → 解压 → 双击 → 跟随首次使用引导 → 选择 Agent。GitHub 确认领取后才会执行。', 'detail-footer-note');
  $('#detail-content').replaceChildren(header, body, footer, note);
  openDialog($('#detail-dialog'));
}

function renderPublisher() {
  const disabled = loading || lastError || snapshot.demo || !snapshot.repository;
  $('#copy-publisher-instructions').disabled = Boolean(disabled);
  $('#publisher-setup').disabled = Boolean(disabled);
  $('#publisher-unavailable').hidden = !disabled;
  $('#publisher-unavailable').textContent = snapshot.demo ? '演示模式不提供真实发布接入。请返回真实看板后复制指引或下载安装包。' : loading ? '正在读取任务仓库，确认后即可使用发布接入。' : '任务仓库尚未连接或快照不可用。请联系维护者配置后，再接入你的 Agent。';
  $('#publisher-instructions').value = disabled ? '' : publisherInstructions(snapshot, document.baseURI);
}

function openDownload({ task, action = 'run' } = {}) {
  if (loading || lastError || snapshot.demo || !snapshot.repository) {
    showToast('请先打开已连接的真实任务看板。', true);
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
  heading.append(element('span', 'eyebrow', action === 'run' ? `QUEST #${task.number} / 准备出发` : 'PUBLISHER SETUP / 委托接入'));
  const title = element('h2', '', action === 'run' ? '选择你的电脑系统' : '接入你正在使用的 Agent');
  title.id = 'download-title';
  heading.append(title, paragraph(action === 'run' ? '下载后按引导完成首次配置，再选择兼容 Agent 执行。' : '下载后选择项目与 Agent。现有 Agent 会在你确认提案后发布委托。'));
  const close = button('', 'icon-button', () => dialog.close(), 'close');
  close.setAttribute('aria-label', '关闭下载');
  header.append(heading, close);
  const body = element('div', 'dialog-body download-body');
  const choices = element('fieldset', 'platform-choices');
  choices.append(element('legend', '', '下载哪个系统的运行包？'));
  const platformNote = paragraph('请选择系统。每个包都包含首次使用引导；无需手动输入初始化命令。', 'download-platform-note');
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
  const download = button('选择系统后下载', 'button primary', async () => {
    if (busy || !selectedPlatform) return;
    if (board !== snapshot || loading || lastError) {
      error.textContent = '任务快照已更新，请关闭下载窗口并重新选择任务。';
      error.hidden = false;
      return;
    }
    busy = true;
    error.hidden = true;
    success.hidden = true;
    progress.textContent = '正在验证运行时完整性并准备下载…';
    progress.hidden = false;
    download.disabled = true;
    for (const input of choices.querySelectorAll('input')) input.disabled = true;
    try {
      const result = await createDownloadPackage({ snapshot: board, task, platform: selectedPlatform, action }, { pageURL: document.baseURI });
      if (!active) return;
      if (board !== snapshot || loading || lastError) throw new Error('任务快照已更新，请关闭下载窗口后重新下载。');
      saveDownload(new Blob([result.bytes], { type: 'application/zip' }), result.filename);
      success.replaceChildren(element('strong', '', '运行包已准备好'), paragraph(`请在浏览器下载列表中找到 ${result.filename}，完整解压后双击 ${DOWNLOAD_PLATFORMS[selectedPlatform].starter}。${action === 'run' ? '下载本身不会领取任务，启动后会再次核验实时状态。' : '启动后按引导为你的项目接入发布 skill。'}`));
      success.hidden = false;
    } catch (issue) {
      if (!active) return;
      error.textContent = issue.message || '下载失败，请检查网络后重试。';
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
      download.replaceChildren(icon('download'), document.createTextNode(`下载 ${platform.label} ${action === 'run' ? '任务包' : '接入包'}`));
      download.disabled = busy;
      success.hidden = true;
      error.hidden = true;
    });
    option.append(radio, element('strong', '', platform.label), element('span', '', platform.starter));
    choices.append(option);
  }
  const steps = element('ol', 'download-steps');
  for (const [headingText, description] of [
    ['完整解压', '把 ZIP 内的所有文件解压到同一个文件夹。'],
    ['双击启动', '打开所选系统的 Start-Taskboard 文件。'],
    ['跟随首次引导', '缺少工具时查看官方安装指引，并完成 GitHub 与 Agent 登录。'],
    ['选择 Agent', action === 'run' ? '确认任务后执行；完成时自动回传交付，并通知发布者验收。' : '选择当前项目与 Agent，在原会话中整理提案、确认发布。'],
  ]) {
    const item = element('li');
    item.append(element('strong', '', headingText), paragraph(description));
    steps.append(item);
  }
  body.append(choices, platformNote, steps, paragraph('需要 Python、Git、GitHub CLI 和所选 Agent；引导会检查并提供安装帮助。此包包含启动脚本，未做原生应用签名。请按系统与组织要求核验来源，不绕过安全保护。', 'download-trust-note'), progress, error, success);
  footer.append(button('返回', 'button secondary', () => dialog.close()), download);
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
  $('#source-host-help').textContent = `使用 ${snapshot.hostname} 下的 HTTPS 仓库地址，不包含 .git 后缀。`;
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
    showToast('任务 JSON 已导出，请使用本地 CLI 发布');
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
$('#copy-publisher-instructions').addEventListener('click', () => copyText($('#publisher-instructions').value, '已复制，请发送给正在使用的 Agent'));
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
