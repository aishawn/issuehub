const state = { kind: 'note', query: '', status: '', source: '', kindFilter: '', selected: null, offset: 0, hasMore: false, requestId: 0 };
const $ = (selector) => document.querySelector(selector);
const detailFocus = createDetailFocus(() => {
  const id = Number(state.selected?.id);
  return id ? document.querySelector(`.record[data-id="${id}"]`) || $('#search') : $('#search');
});
const names = { note: '随笔', issue: '问题', solution: '方案' };
const statusNames = { triage: '待处理', 'in-progress': '处理中', resolved: '已解决' };
const esc = (value) => String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[char]));

function message(text, error = false) {
  const element = $('#form-message');
  element.textContent = text;
  element.classList.toggle('error', error);
  element.setAttribute('role', error ? 'alert' : 'status');
}

async function jsonRequest(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatApiError(body.detail, response.status));
  return body;
}

function restoreResultsFocus(recordId) {
  if (recordId === null) return;
  const target = [...$('#results').querySelectorAll('.record')]
    .find((record) => record.dataset.id === recordId);
  (target || $('#search')).focus({ preventScroll: true });
}

function render(rows, append = false) {
  const box = $('#results');
  const focusedRecordId = !append && box.contains(document.activeElement) && document.activeElement.classList.contains('record')
    ? document.activeElement.dataset.id
    : null;
  if (!append && rows.length === 0) {
    const filtered = Boolean(state.query || state.kindFilter || state.source || state.status);
    box.innerHTML = filtered
      ? '<div class="empty">没有匹配记录<br><span>换个关键词，或调整过滤条件</span></div>'
      : '<div class="empty">还没有记录<br><span>先记录一个问题，团队就能搜回解决过程</span></div>';
    restoreResultsFocus(focusedRecordId);
    return;
  }
  const html = rows.map((record) => `<button class="record" type="button" data-id="${Number(record.id)}" aria-label="查看：${esc(record.title)}">
    <span class="record-title">${esc(record.title)}</span>
    <span class="record-meta"><span class="badge">${esc(names[record.kind] || record.kind)}</span><span>${esc(statusNames[record.status] || record.status)}</span><time datetime="${esc(record.updated_at)}">${new Date(record.updated_at).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })}</time></span>
  </button>`).join('');
  if (!append) box.innerHTML = '';
  box.insertAdjacentHTML('beforeend', html);
  restoreResultsFocus(focusedRecordId);
  box.querySelectorAll('.record:not([data-bound])').forEach((button) => {
    button.dataset.bound = 'true';
    button.addEventListener('click', () => openDetail(button.dataset.id));
  });
}

function updateLoadMore() {
  let button = $('#load-more');
  if (!button) {
    button = document.createElement('button');
    button.id = 'load-more';
    button.className = 'secondary load-more';
    button.type = 'button';
    button.textContent = '加载更多';
    button.addEventListener('click', createLoadMoreHandler(() => loadRecords(true)));
    $('#results').after(button);
  }
  button.hidden = !state.hasMore;
  button.disabled = false;
  button.textContent = '加载更多';
}

async function loadRecords(append = false) {
  if (!append) state.offset = 0;
  const requestId = ++state.requestId;
  const params = new URLSearchParams({ q: state.query, limit: '30', offset: String(state.offset) });
  if (state.kindFilter) params.set('kind', state.kindFilter);
  if (state.status) params.set('status', state.status);
  if (state.source) params.set('source', state.source);
  const button = $('#load-more');
  if (append && button) { button.disabled = true; button.textContent = '正在加载…'; }
  $('#results').setAttribute('aria-busy', 'true');
  try {
    const rows = await jsonRequest(`/v1/search?${params}`);
    if (requestId !== state.requestId) return true;
    render(rows, append);
    state.offset += rows.length;
    state.hasMore = rows.length === 30;
    $('#count').textContent = `${state.offset}${state.hasMore ? '+' : ''} 条`;
    updateLoadMore();
    return true;
  } catch (error) {
    if (requestId === state.requestId) {
      if (!append) {
        const box = $('#results');
        const focusedRecordId = box.contains(document.activeElement) && document.activeElement.classList.contains('record')
          ? document.activeElement.dataset.id
          : null;
        box.innerHTML = `<div class="empty error" role="alert">加载失败：${esc(error.message)}<br><span>请检查服务后重试</span></div>`;
        restoreResultsFocus(focusedRecordId);
      }
      message(`记录加载失败：${error.message}`, true);
    }
    return false;
  } finally {
    if (requestId === state.requestId) $('#results').setAttribute('aria-busy', 'false');
  }
}

async function loadSummary() {
  try {
    const [health, overview] = await Promise.all([jsonRequest('/health'), jsonRequest('/v1/overview')]);
    $('#health').textContent = health.gitlab_configured ? `GitLab 已连接 · ${health.pending_sync ? `${health.pending_sync} 条待同步` : '同步正常'}` : '本地模式';
    $('#health').classList.toggle('warning', health.pending_sync > 0);
    const retryButton = $('#sync-retry');
    retryButton.hidden = !health.gitlab_configured || health.pending_sync === 0;
    retryButton.setAttribute('aria-label', `重试同步，${health.pending_sync} 条待处理`);
    $('#metric-total').textContent = overview.total;
    $('#metric-triage').textContent = overview.by_status.triage || 0;
    $('#metric-progress').textContent = overview.by_status['in-progress'] || 0;
    $('#metric-resolved').textContent = overview.by_status.resolved || 0;
    $('#metric-recent').textContent = overview.recent;
    $('#overview-updated').textContent = `更新于 ${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`;
  } catch (error) {
    $('#health').textContent = '服务状态未知';
    $('#health').classList.add('warning');
  }
}

async function load() {
  await Promise.all([loadRecords(), loadSummary()]);
}

async function openDetail(id) {
  const opener = document.activeElement;
  try {
    const record = await jsonRequest(`/v1/issues/${encodeURIComponent(id)}`);
    state.selected = record;
    $('#detail-title').textContent = record.title;
    const externalUrl = safeExternalUrl(record.external_url);
    $('#detail-meta').innerHTML = `<span class="badge">${esc(names[record.kind] || record.kind)}</span><span>${esc(statusNames[record.status] || record.status)}</span><span>${esc(record.source)}</span><time datetime="${esc(record.updated_at)}">${new Date(record.updated_at).toLocaleString('zh-CN')}</time>${externalUrl ? `<a href="${esc(externalUrl)}" target="_blank" rel="noopener noreferrer">查看 GitLab Issue</a>` : ''}${record.sync_error ? `<span class="sync-error">同步待处理</span>` : ''}`;
    $('#detail-content').textContent = record.content;
    $('#solution-input').value = '';
    $('#detail').classList.remove('hidden');
    $('#resolve-issue').hidden = record.status === 'resolved';
    $('#reopen-issue').hidden = record.status !== 'resolved';
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
    $('#detail').scrollIntoView({ behavior: motion, block: 'start' });
    detailFocus.open(opener, $('#detail-title'));
  } catch (error) {
    message(`无法打开记录：${error.message}`, true);
  }
}

function closeDetail() {
  const detail = $('#detail');
  if (detail.classList.contains('hidden')) return;
  detail.classList.add('hidden');
  detailFocus.close();
}

function setStatusFilter(status, button) {
  state.status = status;
  document.querySelectorAll('[data-filter-status]').forEach((item) => {
    item.classList.toggle('active', item === button);
    item.setAttribute('aria-pressed', String(item === button));
  });
  loadRecords();
}

document.querySelectorAll('[data-kind]').forEach((button) => button.addEventListener('click', () => {
  state.kind = button.dataset.kind;
  document.querySelectorAll('[data-kind]').forEach((item) => {
    item.classList.toggle('active', item === button);
    item.setAttribute('aria-pressed', String(item === button));
  });
}));

document.querySelectorAll('[data-filter-status]').forEach((button) => button.addEventListener('click', () => setStatusFilter(button.dataset.filterStatus, button)));
$('#search').addEventListener('input', (event) => {
  state.query = event.target.value;
  clearTimeout(window.searchTimer);
  window.searchTimer = setTimeout(loadRecords, 250);
});
$('#kind-filter').addEventListener('change', (event) => { state.kindFilter = event.target.value; loadRecords(); });
$('#source-filter').addEventListener('change', (event) => { state.source = event.target.value; loadRecords(); });
$('#close-detail').addEventListener('click', closeDetail);

$('#sync-retry').addEventListener('click', createSyncRetryHandler({ request: jsonRequest, message, refresh: load }));

$('#submit').addEventListener('click', async () => {
  const content = $('#content').value.trim();
  if (!content) { message('请先填写内容。', true); $('#content').focus(); return; }
  const button = $('#submit');
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  message('正在整理并保存…');
  try {
    const endpoint = { note: '/v1/notes', issue: '/v1/issues', solution: '/v1/solutions' }[state.kind];
    const record = await jsonRequest(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: $('#title').value.trim() || null, content, source: $('#source').value, tags: $('#tags').value.split(',').map((tag) => tag.trim()).filter(Boolean) }) });
    message(record.duplicate ? '这条内容已经存在。' : record.sync_error ? `已保存到本地；GitLab 同步待处理：${record.sync_error}` : '已保存，记录已进入团队库。', Boolean(record.sync_error && record.sync_error !== 'GitLab synchronization pending'));
    if (!record.duplicate) { $('#title').value = ''; $('#content').value = ''; $('#tags').value = ''; }
    await load();
  } catch (error) {
    message(error.message, true);
  } finally {
    button.disabled = false;
    button.removeAttribute('aria-busy');
  }
});

async function updateSelected(path) {
  if (!state.selected) return;
  const solution = $('#solution-input').value.trim();
  if (!solution) { message('请填写方案或重新打开的原因。', true); $('#solution-input').focus(); return; }
  const buttons = ['#append-solution', '#resolve-issue', '#reopen-issue'].map($);
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const record = await jsonRequest(`/v1/issues/${state.selected.id}/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ solution, source: 'web' }) });
    const pending = record.sync_error === 'GitLab synchronization pending';
    message(record.sync_error ? (pending ? '本地已更新；GitLab 同步正在排队。' : `本地已更新；GitLab 同步失败：${record.sync_error}`) : '记录已更新。', Boolean(record.sync_error && !pending));
    await openDetail(state.selected.id);
    await load();
  } catch (error) {
    message(error.message, true);
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

$('#append-solution').addEventListener('click', () => updateSelected('append-solution'));
$('#resolve-issue').addEventListener('click', () => updateSelected('resolve'));
$('#reopen-issue').addEventListener('click', () => updateSelected('reopen'));
document.addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') $('#submit').click();
  if (event.key === 'Escape') closeDetail();
});
load();
