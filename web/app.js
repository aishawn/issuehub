const state = { page: 'overview', layout: 'table', kind: 'issue', query: '', status: '', kindFilter: 'issue', noteVisibility: '', filters: { issue: { project: '', workType: '' }, note: { project: '', workType: '' } }, selected: null, offset: 0, hasMore: false, requestId: 0, summary: null, pendingFiles: [], previewUrl: null, previewDownload: null, isAdmin: false, pendingSync: 0 };
const $ = (selector) => document.querySelector(selector);
const detailFocus = createDetailFocus(() => {
  const id = Number(state.selected?.id);
  return id ? document.querySelector(`.page-view:not(.hidden) [data-id="${id}"]`) || $('#search') : $('#search');
});
const names = { note: '随记', issue: '问题', knowledge: '知识' };
const statusNames = { triage: '待处理', 'in-progress': '处理中', resolved: '已解决', published: '已发布' };
const pageNames = { overview: '总览', issues: '问题', notes: '随手记', review: '知识审核', sync: '同步管理', users: '用户管理' };
const ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024;
const ATTACHMENT_MAX_COUNT = 30;
const TEXT_PREVIEW_MAX = 1024 * 1024;
const esc = (value) => String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[char]));
const displayTitle = (title) => String(title || '').replace(/^\s*问题\s*[:：]\s*/, '');
const projectLabel = (project) => project ? esc(project) : '<span class="muted-cell">未归属</span>';
const resultsBox = () => ({ notes: $('#notes-results'), review: $('#review-results') }[state.page] || $('#results'));
const currentUser = () => localStorage.getItem('teamai-username') || '';
const normalizedUser = () => currentUser().trim().toLocaleLowerCase();

function applyAdminVisibility() {
  document.querySelectorAll('.admin-gate').forEach((element) => element.classList.toggle('hidden', !state.isAdmin));
  if (!state.isAdmin && ['review', 'sync', 'users'].includes(state.page)) setPage('overview');
}

async function refreshMe() {
  if (!currentUser()) {
    state.isAdmin = false;
    applyAdminVisibility();
    return null;
  }
  try {
    const me = await jsonRequest('/v1/me');
    state.isAdmin = Boolean(me.is_admin);
    applyAdminVisibility();
    return me;
  } catch (error) {
    state.isAdmin = false;
    applyAdminVisibility();
    return null;
  }
}

function formatFileSize(bytes) {
  const size = Number(bytes) || 0;
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(2)} MB`;
}

function attachmentExt(fileName) {
  const name = String(fileName || '').toLowerCase();
  const index = name.lastIndexOf('.');
  return index >= 0 ? name.slice(index) : '';
}

function attachmentPreviewKind(fileName, contentType = '') {
  const type = String(contentType || '').toLowerCase().split(';', 1)[0].trim();
  const ext = attachmentExt(fileName);
  if (type.startsWith('image/') || ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'].includes(ext)) return 'image';
  if (type === 'application/pdf' || ext === '.pdf') return 'pdf';
  if (type.startsWith('audio/') || ['.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac'].includes(ext)) return 'audio';
  if (type.startsWith('video/') || ['.mp4', '.webm', '.ogv', '.mov'].includes(ext)) return 'video';
  if (type === 'text/html' || type === 'application/xhtml+xml' || ['.html', '.htm'].includes(ext)) return 'html';
  if (type === 'text/markdown' || ['.md', '.markdown'].includes(ext)) return 'markdown';
  if (
    type.startsWith('text/')
    || type === 'application/json'
    || type === 'application/xml'
    || ['.txt', '.csv', '.json', '.log', '.xml', '.yml', '.yaml', '.css', '.js', '.ts', '.py', '.sh', '.ini', '.conf'].includes(ext)
  ) return 'text';
  return 'other';
}

function canUploadToSelected() {
  const record = state.selected;
  if (!record) return false;
  if (record.kind !== 'note') return true;
  return Boolean(currentUser()) && record.owner_username === currentUser().trim().toLocaleLowerCase();
}

function namedClipboardFile(file) {
  if (file.name && file.name !== 'blob') return file;
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const ext = { 'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp', 'application/pdf': '.pdf', 'text/plain': '.txt' }[file.type] || '';
  return new File([file], `paste-${stamp}${ext}`, { type: file.type || 'application/octet-stream' });
}

function clipboardFiles(event) {
  const files = [];
  for (const item of event.clipboardData?.items || []) {
    if (item.kind === 'file') {
      const file = item.getAsFile();
      if (file) files.push(namedClipboardFile(file));
    }
  }
  if (!files.length) {
    for (const file of event.clipboardData?.files || []) files.push(namedClipboardFile(file));
  }
  return files;
}

function renderPendingAttachments() {
  const list = $('#capture-attachment-list');
  if (!list) return;
  if (!state.pendingFiles.length) {
    list.innerHTML = '<li class="activity-empty">暂无待上传附件</li>';
    return;
  }
  list.innerHTML = state.pendingFiles.map((item, index) => `<li><div class="attachment-main"><span class="attachment-name">${esc(item.name)}</span><span class="attachment-meta">${formatFileSize(item.size)}</span></div><button type="button" class="text-button" data-remove-pending="${index}">移除</button></li>`).join('');
}

function addPendingFiles(files) {
  const incoming = [...files].filter(Boolean);
  if (!incoming.length) return;
  const room = ATTACHMENT_MAX_COUNT - state.pendingFiles.length;
  if (room <= 0) { message(`一次最多添加 ${ATTACHMENT_MAX_COUNT} 个附件。`, true); return; }
  const accepted = [];
  for (const file of incoming.slice(0, room)) {
    if (file.size > ATTACHMENT_MAX_BYTES) { message(`“${file.name}”超过 20 MB，已跳过。`, true); continue; }
    if (!file.size) { message(`“${file.name || '未命名文件'}”为空，已跳过。`, true); continue; }
    accepted.push(file);
  }
  if (!accepted.length) return;
  state.pendingFiles.push(...accepted);
  if (incoming.length > room) message(`一次最多 ${ATTACHMENT_MAX_COUNT} 个附件，多余文件未加入。`, true);
  renderPendingAttachments();
}

function clearPendingFiles() {
  state.pendingFiles = [];
  renderPendingAttachments();
}

async function uploadAttachmentFile(recordId, file) {
  const body = new FormData();
  body.append('file', file, file.name);
  return jsonRequest(`/v1/records/${recordId}/attachments`, { method: 'POST', body });
}

async function uploadFilesToSelected(files) {
  if (!state.selected || !canUploadToSelected()) return;
  const list = [...files].filter(Boolean);
  if (!list.length) return;
  const existing = (state.selected.attachments || []).length;
  const room = ATTACHMENT_MAX_COUNT - existing;
  if (room <= 0) { message(`一条记录最多 ${ATTACHMENT_MAX_COUNT} 个附件。`, true); return; }
  let uploaded = 0;
  for (const file of list.slice(0, room)) {
    if (file.size > ATTACHMENT_MAX_BYTES) { message(`“${file.name}”超过 20 MB，已跳过。`, true); continue; }
    if (!file.size) { message(`“${file.name || '未命名文件'}”为空，已跳过。`, true); continue; }
    try {
      await uploadAttachmentFile(state.selected.id, file);
      uploaded += 1;
    } catch (error) {
      message(`附件上传失败：${error.message}`, true);
      if (uploaded) await openDetail(state.selected.id);
      return;
    }
  }
  if (uploaded) {
    message(uploaded === 1 ? '附件已添加。' : `已添加 ${uploaded} 个附件。`);
    await openDetail(state.selected.id);
  }
}

async function fetchAttachmentBlob(recordId, attachmentId, { inline = false } = {}) {
  const url = `/v1/records/${Number(recordId)}/attachments/${Number(attachmentId)}${inline ? '?inline=true' : ''}`;
  const response = await fetch(url, { headers: { 'X-TeamAI-User': encodeURIComponent(currentUser()) } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return { blob: await response.blob(), response };
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename || 'attachment';
  link.click();
  URL.revokeObjectURL(url);
}

function closeAttachmentPreview() {
  const dialog = $('#attachment-preview');
  if (!dialog || dialog.classList.contains('hidden')) return;
  dialog.classList.add('hidden');
  $('#attachment-preview-body').innerHTML = '';
  if (state.previewUrl) {
    URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = null;
  }
  state.previewDownload = null;
}

async function openAttachmentPreview({ recordId, attachmentId, fileName, contentType }) {
  const kind = attachmentPreviewKind(fileName, contentType);
  closeAttachmentPreview();
  $('#attachment-preview-title').textContent = fileName || '附件';
  const body = $('#attachment-preview-body');
  body.innerHTML = '<p class="review-hint">正在加载预览…</p>';
  $('#attachment-preview').classList.remove('hidden');
  state.previewDownload = { recordId, attachmentId, fileName };
  try {
    if (kind === 'other') {
      body.innerHTML = `<div class="preview-fallback"><p>此格式暂不支持在线预览（如图片、PDF、Markdown、HTML、文本、音视频可预览）。</p><button type="button" class="primary" data-preview-download-now>下载文件</button></div>`;
      return;
    }
    const { blob } = await fetchAttachmentBlob(recordId, attachmentId, { inline: true });
    const typed = contentType && (!blob.type || blob.type === 'application/octet-stream')
      ? new Blob([blob], { type: contentType })
      : blob;
    if (kind === 'image') {
      state.previewUrl = URL.createObjectURL(typed);
      body.innerHTML = `<img class="preview-image" src="${state.previewUrl}" alt="${esc(fileName)}">`;
    } else if (kind === 'pdf') {
      state.previewUrl = URL.createObjectURL(typed);
      body.innerHTML = `<iframe class="preview-frame" title="${esc(fileName)}" src="${state.previewUrl}"></iframe>`;
    } else if (kind === 'audio') {
      state.previewUrl = URL.createObjectURL(typed);
      body.innerHTML = `<audio class="preview-media" controls src="${state.previewUrl}"></audio>`;
    } else if (kind === 'video') {
      state.previewUrl = URL.createObjectURL(typed);
      body.innerHTML = `<video class="preview-media preview-video" controls src="${state.previewUrl}"></video>`;
    } else if (kind === 'markdown') {
      const text = await typed.slice(0, TEXT_PREVIEW_MAX + 1).text();
      const truncated = text.length > TEXT_PREVIEW_MAX;
      const source = truncated ? text.slice(0, TEXT_PREVIEW_MAX) : text;
      body.innerHTML = `${truncated ? '<p class="review-hint">文件较大，仅预览前 1 MB。</p>' : ''}<div class="preview-markdown detail-content">${renderMarkdown(source)}</div>`;
    } else if (kind === 'html') {
      const text = await typed.slice(0, TEXT_PREVIEW_MAX + 1).text();
      const truncated = text.length > TEXT_PREVIEW_MAX;
      const source = truncated ? text.slice(0, TEXT_PREVIEW_MAX) : text;
      state.previewUrl = URL.createObjectURL(new Blob([source], { type: 'text/html;charset=utf-8' }));
      body.innerHTML = `${truncated ? '<p class="review-hint">文件较大，仅预览前 1 MB。脚本与外链交互已禁用。</p>' : '<p class="review-hint">沙箱预览：脚本已禁用，样式与布局可查看。</p>'}<iframe class="preview-frame preview-html" title="${esc(fileName)}" src="${state.previewUrl}" sandbox referrerpolicy="no-referrer"></iframe>`;
    } else if (kind === 'text') {
      const text = await typed.slice(0, TEXT_PREVIEW_MAX + 1).text();
      const truncated = text.length > TEXT_PREVIEW_MAX;
      body.innerHTML = `${truncated ? '<p class="review-hint">文件较大，仅预览前 1 MB。</p>' : ''}<pre class="preview-text">${esc(truncated ? text.slice(0, TEXT_PREVIEW_MAX) : text)}</pre>`;
    }
  } catch (error) {
    body.innerHTML = `<div class="preview-fallback"><p>预览失败：${esc(error.message)}</p><button type="button" class="primary" data-preview-download-now>改为下载</button></div>`;
  }
}

function showUserLogin(switching = false) {
  $('#username-input').value = currentUser();
  $('#user-login-title').textContent = switching ? '切换用户名' : '进入 TeamAI';
  $('#user-login').classList.remove('hidden');
  $('#username-input').focus();
}

function updateUserControl() {
  const name = currentUser();
  const role = state.isAdmin ? ' · 管理员' : '';
  $('#user-switch').textContent = name ? `用户名：${name}${role}` : '设置用户名';
  $('#user-login').classList.toggle('hidden', Boolean(name));
}

function message(text, error = false) {
  const element = $('#form-message');
  element.textContent = text;
  element.classList.toggle('error', error);
  element.setAttribute('role', error ? 'alert' : 'status');
  clearTimeout(window.messageTimer);
  window.messageTimer = setTimeout(() => {
    if (element.textContent === text) element.textContent = '';
  }, error ? 8000 : 5000);
}

async function jsonRequest(url, options) {
  const headers = new Headers(options?.headers || {});
  if (currentUser()) headers.set('X-TeamAI-User', encodeURIComponent(currentUser()));
  const response = await fetch(url, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatApiError(body.detail, response.status));
  return body;
}

function restoreResultsFocus(recordId) {
  if (recordId === null) return;
  const target = [...resultsBox().querySelectorAll('[data-id]')]
    .find((record) => record.dataset.id === recordId);
  (target || $('#search')).focus({ preventScroll: true });
}

function render(rows, append = false) {
  const box = resultsBox();
  const focusedRecordId = !append && box.contains(document.activeElement) && document.activeElement.closest('[data-id]')
    ? document.activeElement.closest('[data-id]').dataset.id
    : null;
  if (!append && rows.length === 0) {
    const activeFilters = state.filters[state.kindFilter] || {};
    const filtered = Boolean(state.query || state.status || state.noteVisibility || activeFilters.project || activeFilters.workType);
    if (state.page === 'notes') {
      box.innerHTML = filtered
        ? `<div class="empty"><div class="empty-copy"><strong>还没有符合条件的随手记</strong><span>调整可见范围或搜索关键词</span></div></div>`
        : `<div class="empty"><div class="empty-copy"><strong>还没有随手记</strong><span>可以先记录一条想法、资料或待办</span></div></div>`;
    } else if (state.page === 'review') {
      box.innerHTML = `<div class="empty"><div class="empty-copy"><strong>暂无待审核项</strong><span>解决后默认自动发布；此处仅显示遗留队列</span></div></div>`;
    } else {
      box.innerHTML = filtered
        ? `<div class="empty"><div class="empty-copy"><strong>没有匹配记录</strong><span>换个关键词，或调整筛选条件</span></div></div>`
        : `<div class="empty"><div class="empty-copy"><strong>还没有问题记录</strong><span>可通过右上角新建一条问题</span></div></div>`;
    }
    restoreResultsFocus(focusedRecordId);
    return;
  }
  const html = rows.map((record) => {
    const title = displayTitle(record.title);
    const visibilityBadge = record.kind === 'note' ? `<span class="record-visibility">${record.visibility === 'private' ? '个人视图' : '团队共享'}</span>` : '';
    const preview = record.kind === 'note' ? `<span class="record-preview">${esc((record.content || '').replace(/\s+/g, ' ').slice(0, 180))}${record.content?.length > 180 ? '…' : ''}</span>` : '';
    return `<button class="record" type="button" data-id="${Number(record.id)}" aria-label="查看：${esc(title)}"><span class="record-title">${esc(title)}</span>${preview}<span class="record-meta"><span class="badge">${esc(names[record.kind] || record.kind)}</span>${record.project ? `<span>${esc(record.project)}</span>` : ''}${visibilityBadge}<span>${esc(statusNames[record.status] || record.status)}</span><time datetime="${esc(record.updated_at)}">${new Date(record.updated_at).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })}</time></span></button>`;
  }).join('');
  if (!append) box.innerHTML = '';
  box.insertAdjacentHTML('beforeend', html);
  box.querySelectorAll('.record:not([data-bound])').forEach((button) => {
    button.dataset.bound = 'true';
    button.addEventListener('click', () => openDetail(button.dataset.id));
  });
  if (!append && state.page === 'issues' && state.layout === 'table') {
    box.innerHTML = `<div class="table-scroll"><table class="issue-table"><thead><tr><th>问题</th><th>所属项目</th><th>所属类型</th><th>状态</th><th>最近更新</th></tr></thead><tbody>${rows.map((record) => `<tr class="issue-row" data-id="${Number(record.id)}" tabindex="0"><td><b>${esc(displayTitle(record.title))}</b><small>#${Number(record.id)}</small></td><td>${projectLabel(record.project)}</td><td>${esc(record.work_type || '未分类')}</td><td><span class="status-chip status-${esc(record.status)}">${esc(statusNames[record.status] || record.status)}</span></td><td>${new Date(record.updated_at).toLocaleDateString('zh-CN')}</td></tr>`).join('')}</tbody></table></div>`;
    box.querySelectorAll('.issue-row').forEach((row) => {
      const open = () => openDetail(row.dataset.id);
      row.addEventListener('click', open);
      row.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
    });
  } else if (!append && state.page === 'issues' && state.layout === 'board') renderBoard(rows, box);
  restoreResultsFocus(focusedRecordId);
}

function renderBoard(rows, box) {
  const columns = [{ key: 'triage', title: '待处理' }, { key: 'in-progress', title: '处理中' }, { key: 'resolved', title: '已解决' }];
  box.innerHTML = `<div class="board-grid">${columns.map((column) => {
    const items = rows.filter((record) => record.kind === 'issue' && record.status === column.key);
  return `<section class="board-column"><header><b>${column.title}</b><span>${items.length}</span></header>${items.length ? items.map((record) => `<button class="board-card" type="button" data-id="${Number(record.id)}"><b>${esc(displayTitle(record.title))}</b><span>${esc(record.project || '未归属')} · ${esc(record.work_type || '未分类')}</span><small>#${Number(record.id)}</small></button>`).join('') : '<div class="board-empty">暂无记录</div>'}</section>`;
  }).join('')}</div>`;
  box.querySelectorAll('.board-card').forEach((button) => button.addEventListener('click', () => openDetail(button.dataset.id)));
}

function syncTopNewRecord(page) {
  const button = $('#top-new-record');
  const label = button.querySelector('.btn-label');
  const isNotes = page === 'notes';
  button.dataset.captureKind = isNotes ? 'note' : 'issue';
  if (label) label.textContent = isNotes ? '新建随手记' : '新建记录';
  button.setAttribute('aria-label', isNotes ? '新建随手记' : '新建记录');
}

async function loadFilterOptions(kind) {
  const prefix = { note: 'notes', issue: 'issues' }[kind] || kind;
  try {
    const options = await jsonRequest(`/v1/filter-options?kind=${kind}`);
    const projectSelect = $(`#${prefix}-project-filter`);
    const workTypeSelect = $(`#${prefix}-work-type-filter`);
    if (!projectSelect || !workTypeSelect) return;
    const filters = state.filters[kind];
    if (!filters) return;
    const previousProject = filters.project;
    const previousWorkType = filters.workType;
    projectSelect.innerHTML = `<option value="">全部项目</option><option value="__unassigned__">未指定项目</option>${options.projects.map((value) => `<option value="${esc(value)}">${esc(value)}</option>`).join('')}`;
    workTypeSelect.innerHTML = `<option value="">全部工作类型</option>${options.work_types.map((value) => `<option value="${esc(value)}">${esc(value)}</option>`).join('')}`;
    filters.project = [...projectSelect.options].some((option) => option.value === previousProject) ? previousProject : '';
    filters.workType = [...workTypeSelect.options].some((option) => option.value === previousWorkType) ? previousWorkType : '';
    projectSelect.value = filters.project;
    workTypeSelect.value = filters.workType;
    if ((filters.project !== previousProject || filters.workType !== previousWorkType) && state.kindFilter === kind) loadRecords();
  } catch (error) {
    message(`筛选选项加载失败：${error.message}`, true);
  }
}

function setPage(page) {
  if (['review', 'sync', 'users'].includes(page) && !state.isAdmin) {
    message('仅管理员可访问该功能。', true);
    page = 'overview';
  }
  const changed = state.page !== page;
  if (changed) {
    closeDetail(document.activeElement);
    window.scrollTo({ top: 0, behavior: 'auto' });
  }
  state.page = page;
  $('#page-title').textContent = pageNames[page] || '总览';
  syncTopNewRecord(page);
  const searchHints = {
    notes: ['搜索当前随手记', '搜索随手记…'],
    issues: ['搜索当前问题', '搜索问题…'],
    review: ['搜索待审核', '搜索待审核…'],
  };
  const [context, placeholder] = searchHints[page] || ['搜索问题', '搜索问题…'];
  $('#search-context').textContent = context;
  $('#search').placeholder = placeholder;
  document.querySelectorAll('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.page === page));
  document.querySelectorAll('.page-view').forEach((view) => {
    const isAdminView = view.classList.contains('admin-only');
    const match = view.dataset.view === page;
    view.classList.toggle('hidden', !match || (isAdminView && !state.isAdmin));
  });
  if (page === 'issues') { state.kindFilter = 'issue'; state.status = $('#status-filter').value; loadFilterOptions('issue'); loadRecords(); }
  if (page === 'notes') { state.kindFilter = 'note'; state.status = ''; loadFilterOptions('note'); loadRecords(); }
  if (page === 'review') { state.kindFilter = 'issue'; loadReviewQueue(); }
  if (page === 'sync') { refreshSyncStatus(); }
  if (page === 'users') { loadUsers(); }
  if (page === 'overview') loadSummary();
}

function updateLoadMore() {
  if (!['issues', 'notes'].includes(state.page)) return;
  const buttonId = { notes: '#notes-load-more', issues: '#load-more' }[state.page];
  let button = $(buttonId);
  if (!button) {
    button = document.createElement('button');
    button.id = buttonId.slice(1);
    button.className = 'secondary load-more';
    button.type = 'button';
    button.textContent = '加载更多';
    button.addEventListener('click', createLoadMoreHandler(() => loadRecords(true)));
    resultsBox()?.after(button);
  }
  button.hidden = !state.hasMore;
  button.disabled = false;
  button.textContent = '加载更多';
}

async function loadRecords(append = false) {
  if (!['issues', 'notes'].includes(state.page)) return true;
  if (!append) state.offset = 0;
  const requestId = ++state.requestId;
  const params = new URLSearchParams({ q: state.query, limit: '30', offset: String(state.offset) });
  if (state.kindFilter) params.set('kind', state.kindFilter);
  if (state.status) params.set('status', state.status);
  if (state.page === 'notes' && state.noteVisibility) params.set('visibility', state.noteVisibility);
  const filters = state.filters[state.kindFilter] || {};
  if (filters.project) params.set('project', filters.project);
  if (filters.workType) params.set('work_type', filters.workType);
  const button = ({ notes: $('#notes-load-more'), issues: $('#load-more') })[state.page];
  if (append && button) { button.disabled = true; button.textContent = '正在加载…'; }
  resultsBox().setAttribute('aria-busy', 'true');
  try {
    const rows = await jsonRequest(`/v1/search?${params}`);
    if (requestId !== state.requestId) return true;
    render(rows, append);
    state.offset += rows.length;
    state.hasMore = rows.length === 30;
    const count = ({ notes: $('#notes-count'), issues: $('#count') })[state.page];
    if (count) count.textContent = `${state.offset}${state.hasMore ? '+' : ''} 条`;
    updateLoadMore();
    return true;
  } catch (error) {
    if (requestId === state.requestId) {
      if (!append) {
        const box = resultsBox();
        const focusedRecordId = box.contains(document.activeElement) ? document.activeElement.dataset.id || document.activeElement.closest('[data-id]')?.dataset.id : null;
        box.innerHTML = `<div class="empty error" role="alert">加载失败：${esc(error.message)}<br><span>请检查服务后重试</span></div>`;
        if (focusedRecordId) box.querySelector(`[data-id="${CSS.escape(focusedRecordId)}"]`)?.focus(); else $('#search').focus({ preventScroll: true });
      }
      message(`记录加载失败：${error.message}`, true);
    }
    return false;
  } finally {
    if (requestId === state.requestId) resultsBox().setAttribute('aria-busy', 'false');
  }
}

async function loadReviewQueue() {
  if (!state.isAdmin) return;
  const box = $('#review-results');
  $('#review-editor').classList.add('hidden');
  box.setAttribute('aria-busy', 'true');
  try {
    const rows = await jsonRequest('/v1/knowledge/candidates');
    $('#nav-review').textContent = String(rows.length);
    $('#review-count').textContent = `${rows.length} 条`;
    if (!rows.length) {
      render([], false);
      return;
    }
    box.innerHTML = rows.map((record) => `<button class="record" type="button" data-review-id="${Number(record.id)}"><span class="record-title">${esc(displayTitle(record.title))}</span><span class="record-meta"><span class="badge">待审核</span>${record.project ? `<span>${esc(record.project)}</span>` : ''}<span>${esc(record.knowledge_status)}</span></span></button>`).join('');
    box.querySelectorAll('[data-review-id]').forEach((button) => button.addEventListener('click', () => openReviewEditor(button.dataset.reviewId)));
  } catch (error) {
    box.innerHTML = `<div class="empty error" role="alert">加载失败：${esc(error.message)}</div>`;
  } finally {
    box.setAttribute('aria-busy', 'false');
  }
}

async function openReviewEditor(id) {
  try {
    const record = await jsonRequest(`/v1/issues/${encodeURIComponent(id)}`);
    $('#review-issue-id').value = String(record.id);
    $('#review-title').value = displayTitle(record.title);
    $('#review-content').value = record.content;
    $('#review-editor-title').textContent = `整理并发布 #${record.id}`;
    $('#review-editor').classList.remove('hidden');
    $('#review-title').focus();
  } catch (error) {
    message(`无法打开审核项：${error.message}`, true);
  }
}

async function loadUsers() {
  if (!state.isAdmin) return;
  try {
    const payload = await jsonRequest('/v1/admin/users');
    const rows = payload.users || [];
    $('#users-empty').classList.toggle('hidden', rows.length > 0);
    $('#users-rows').innerHTML = rows.map((user) => `<tr><td><b>${esc(user.username)}</b></td><td>${user.is_admin ? '管理员' : '用户'}</td><td>${Number(user.note_count) || 0}</td><td>${Number(user.issue_count) || 0}</td><td>${Number(user.event_count) || 0}</td><td>${user.last_active_at ? new Date(user.last_active_at).toLocaleString('zh-CN') : '—'}</td></tr>`).join('');
  } catch (error) {
    message(`用户列表加载失败：${error.message}`, true);
  }
}

function updateSyncStatusUI(pending) {
  state.pendingSync = Number(pending) || 0;
  const text = state.pendingSync > 0 ? `有 ${state.pendingSync} 条记录等待同步到 GitLab。` : '当前没有待同步记录。';
  ['#overview-sync-status', '#sync-manage-status'].forEach((selector) => {
    const element = $(selector);
    if (element) element.textContent = text;
  });
  ['#overview-sync-retry', '#sync-retry'].forEach((selector) => {
    const button = $(selector);
    if (!button) return;
    button.hidden = !state.isAdmin || state.pendingSync <= 0;
  });
}

async function refreshSyncStatus() {
  if (!state.isAdmin) return;
  try {
    const health = await jsonRequest('/health');
    updateSyncStatusUI(health.pending_sync || 0);
  } catch (error) {
    ['#overview-sync-status', '#sync-manage-status'].forEach((selector) => {
      const element = $(selector);
      if (element) element.textContent = `无法读取同步状态：${error.message}`;
    });
  }
}

async function loadSummary() {
  try {
    const [health, overview] = await Promise.all([jsonRequest('/health'), jsonRequest('/v1/overview')]);
    $('#health').textContent = health.gitlab_configured ? 'GitLab 已连接' : '本地模式';
    $('#health').classList.remove('warning');
    $('#metric-total').textContent = overview.by_kind?.issue || 0;
    $('#metric-triage').textContent = overview.by_status.triage || 0;
    $('#metric-progress').textContent = overview.by_status['in-progress'] || 0;
    $('#metric-resolved').textContent = overview.by_status.resolved || 0;
    $('#metric-recent').textContent = overview.recent;
    state.summary = overview;
    $('#nav-issues').textContent = Object.values(overview.by_status || {}).reduce((sum, count) => sum + count, 0);
    if (state.isAdmin) {
      $('#nav-review').textContent = String(overview.pending_knowledge || 0);
      updateSyncStatusUI(health.pending_sync || 0);
    }
    $('#overview-updated').textContent = `更新于 ${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`;
    const recent = await jsonRequest('/v1/search?kind=issue&limit=6');
    $('#overview-rows').innerHTML = recent.map((record) => `<tr class="issue-row" data-id="${Number(record.id)}" tabindex="0"><td><b>${esc(displayTitle(record.title))}</b><small>#${Number(record.id)}</small></td><td>${projectLabel(record.project)}</td><td>${esc(record.work_type || '未分类')}</td><td><span class="status-chip status-${esc(record.status)}">${esc(statusNames[record.status] || record.status)}</span></td><td>${new Date(record.updated_at).toLocaleDateString('zh-CN')}</td></tr>`).join('');
    $('#overview-empty').classList.toggle('hidden', recent.length > 0);
    document.querySelector('.overview-list .table-scroll')?.classList.toggle('hidden', recent.length === 0);
    $('#overview-rows').querySelectorAll('.issue-row').forEach((row) => {
      const open = () => openDetail(row.dataset.id);
      row.addEventListener('click', open);
      row.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
    });
  } catch (error) {
    $('#health').textContent = '服务状态未知';
    $('#health').classList.add('warning');
  }
}

async function load() {
  await refreshMe();
  updateUserControl();
  await Promise.all([loadSummary(), loadRecords()]);
}

function renderMarkdown(source) {
  const lines = String(source || '').replace(/\r\n?/g, '\n').split('\n');
  // Hide template headings that have no content before the next heading.
  const visibleLines = lines.filter((line, index) => {
    if (!/^\s{0,3}#{1,3}\s+.+$/.test(line)) return true;
    const nextContent = lines.slice(index + 1).find((candidate) => candidate.trim());
    return Boolean(nextContent && !/^\s{0,3}#{1,3}\s+.+$/.test(nextContent));
  });
  const output = [];
  let paragraph = [];
  let listType = '';
  let codeLines = [];
  let inCode = false;
  const inline = (text) => esc(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|\s)\*(\S(?:.*?\S)?)\*(?=\s|$)/g, '$1<em>$2</em>');
  const flushParagraph = () => {
    if (paragraph.length) output.push(`<p>${paragraph.map(inline).join('<br>')}</p>`);
    paragraph = [];
  };
  const closeList = () => {
    if (listType) output.push(`</${listType}>`);
    listType = '';
  };
  for (const line of visibleLines) {
    if (/^\s*```/.test(line)) {
      flushParagraph(); closeList();
      if (inCode) { output.push(`<pre class="content-code"><code>${esc(codeLines.join('\n'))}</code></pre>`); codeLines = []; }
      inCode = !inCode;
      continue;
    }
    if (inCode) { codeLines.push(line); continue; }
    const heading = line.match(/^\s{0,3}(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph(); closeList();
      output.push(`<h3>${inline(heading[2])}</h3>`);
      continue;
    }
    const item = line.match(/^\s*(?:([-*+])|(\d+)\.)\s+(.+)$/);
    if (item) {
      flushParagraph();
      const nextType = item[2] ? 'ol' : 'ul';
      if (listType !== nextType) { closeList(); output.push(`<${nextType}>`); listType = nextType; }
      const task = item[3].match(/^\[([ xX])\]\s+(.*)$/);
      output.push(`<li>${task ? `<span class="task-mark">${task[1] === ' ' ? '○' : '✓'}</span> ${inline(task[2])}` : inline(item[3])}</li>`);
      continue;
    }
    if (!line.trim()) { flushParagraph(); closeList(); continue; }
    closeList(); paragraph.push(line.trim());
  }
  flushParagraph(); closeList();
  if (inCode) output.push(`<pre class="content-code"><code>${esc(codeLines.join('\n'))}</code></pre>`);
  return output.join('') || '<p class="content-empty">暂无正文</p>';
}

async function openDetail(id) {
  const opener = document.activeElement;
  $('#reader-error').classList.add('hidden');
  $('#reader-error').textContent = '';
  try {
    const record = await jsonRequest(`/v1/issues/${encodeURIComponent(id)}`);
    state.selected = record;
    $('#expand-reading').href = `/web/index.html#record=${encodeURIComponent(record.id)}`;
    $('#detail-title').textContent = displayTitle(record.title);
    const externalUrl = safeExternalUrl(record.external_url);
    const project = record.project ? `<span>所属项目：${esc(record.project)}</span>` : '<span class="muted-cell">未归属项目</span>';
    const workType = `<span>所属类型：${esc(record.work_type || '未分类')}</span>`;
    const visibility = record.kind === 'note' ? `<span class="record-visibility">${record.visibility === 'private' ? `个人视图 · ${esc(record.owner_username || '')}` : '团队共享'}</span>` : '';
    const statusChip = `<span class="status-chip status-${esc(record.status)}">${esc(statusNames[record.status] || record.status)}</span>`;
    const ownsNote = record.kind === 'note' && Boolean(currentUser()) && record.owner_username === normalizedUser();
    const canManageAttachments = (record.kind === 'issue' || record.kind === 'note') && (record.kind !== 'note' || ownsNote) && Boolean(currentUser());
    const parentIssue = record.kind === 'knowledge' && record.parent_issue_id
      ? `<button type="button" class="text-button" data-open-parent="${Number(record.parent_issue_id)}">查看来源问题 #${Number(record.parent_issue_id)}</button>`
      : '';
    $('#detail-meta').innerHTML = `<span class="badge">${esc(names[record.kind] || record.kind)}</span>${statusChip}${project}${workType}${visibility}<time datetime="${esc(record.updated_at)}">${new Date(record.updated_at).toLocaleString('zh-CN')}</time>${externalUrl ? `<a href="${esc(externalUrl)}" target="_blank" rel="noopener noreferrer">查看 GitLab Issue</a>` : ''}${parentIssue}`;
    $('#detail-content').innerHTML = renderMarkdown(record.content);
    const attachmentList = $('#attachment-list');
    attachmentList.innerHTML = (record.attachments || []).map((file) => {
      const kind = attachmentPreviewKind(file.file_name, file.content_type);
      const url = `/v1/records/${Number(record.id)}/attachments/${Number(file.id)}`;
      const previewAction = kind === 'other'
        ? ''
        : `<button type="button" class="text-button" data-preview-attachment data-record-id="${Number(record.id)}" data-attachment-id="${Number(file.id)}" data-filename="${esc(file.file_name)}" data-content-type="${esc(file.content_type || '')}">预览</button>`;
      const deleteAction = canManageAttachments
        ? `<button type="button" class="text-button danger-text" data-delete-attachment data-record-id="${Number(record.id)}" data-attachment-id="${Number(file.id)}" data-filename="${esc(file.file_name)}">删除</button>`
        : '';
      return `<li><div class="attachment-main"><a href="${url}" data-attachment-url="${url}" data-record-id="${Number(record.id)}" data-attachment-id="${Number(file.id)}" data-filename="${esc(file.file_name)}" data-content-type="${esc(file.content_type || '')}" data-preview-kind="${kind}">${esc(file.file_name)}</a><span class="attachment-meta">${formatFileSize(file.size_bytes)}</span></div><div class="attachment-actions">${previewAction}<button type="button" class="text-button" data-download-attachment data-record-id="${Number(record.id)}" data-attachment-id="${Number(file.id)}" data-filename="${esc(file.file_name)}">下载</button>${deleteAction}</div></li>`;
    }).join('') || '<li class="activity-empty">暂无附件</li>';
    const events = record.events || [{ event_type: 'created', summary: '记录已创建', created_at: record.created_at, actor: record.author_id || '' }];
    $('#activity-list').innerHTML = events.length ? events.map((event) => `<li class="activity-item"><span class="activity-dot"></span><div><b>${esc(event.summary)}</b><small>${event.actor ? `${esc(event.actor)} · ` : ''}${new Date(event.created_at).toLocaleString('zh-CN')}</small></div></li>`).join('') : '<li class="activity-empty">暂无活动记录</li>';
    const canDelete = (record.kind === 'note' && ownsNote) || (record.kind === 'issue' && Boolean(currentUser()));
    const deleteButton = $('#delete-record');
    deleteButton.hidden = !canDelete;
    deleteButton.textContent = record.kind === 'note' ? '删除随手记' : record.kind === 'issue' ? '删除问题' : '删除';
    $('#detail .attachment-upload').classList.toggle('hidden', !canManageAttachments);
    $('#record-editor').classList.add('hidden');
    $('#detail-content').classList.remove('hidden');
    const canEdit = Boolean(currentUser()) && (
      (record.kind === 'note' && ownsNote)
      || (record.kind === 'issue' && record.status !== 'resolved')
    );
    $('#edit-record').hidden = !canEdit;
    $('#edit-title').value = record.title;
    $('#edit-content').value = record.content;
    $('#edit-project').value = record.project || '';
    $('#edit-work-type').value = record.work_type || '';
    $('#edit-visibility-row').classList.toggle('hidden', record.kind !== 'note');
    $('#edit-visibility').value = record.visibility || 'shared';
    $('#solution-input').value = '';
    $('#verification-input').value = '';
    $('#detail').classList.remove('hidden');
    $('#detail-backdrop').classList.remove('hidden');
    document.body.classList.add('detail-open');
    $('#resolve-issue').hidden = record.status === 'resolved';
    $('#reopen-issue').hidden = record.status !== 'resolved';
    $('#issue-actions').classList.toggle('hidden', record.kind !== 'issue');
    detailFocus.open(opener, $('#detail-title'));
  } catch (error) {
    if (document.body.classList.contains('reader-mode')) {
      $('#reader-error').textContent = `无法打开记录：${error.message}`;
      $('#reader-error').classList.remove('hidden');
    } else message(`无法打开记录：${error.message}`, true);
  }
}

function closeDetail(returnFocusTo) {
  if (!$('#attachment-preview')?.classList.contains('hidden')) {
    closeAttachmentPreview();
    return;
  }
  if (document.body.classList.contains('reader-mode')) {
    window.location.assign('/');
    return;
  }
  const detail = $('#detail');
  if (detail.classList.contains('hidden')) return;
  detail.classList.add('hidden');
  $('#detail-backdrop').classList.add('hidden');
  document.body.classList.remove('detail-open');
  detailFocus.close(returnFocusTo);
}

function openCapture(kind = 'issue') {
  state.kind = kind;
  document.querySelectorAll('[data-kind]').forEach((item) => {
    const active = item.dataset.kind === state.kind;
    item.classList.toggle('active', active);
    item.setAttribute('aria-pressed', String(active));
  });
  $('#capture-title').textContent = state.kind === 'note' ? '新建随手记' : '新建问题';
  $('#content').placeholder = state.kind === 'note' ? '记下想法、资料、临时发现或待办…' : '记录问题现象、处理过程和验证结果…';
  $('#capture').classList.remove('hidden');
  $('#note-visibility').value = 'shared';
  $('#note-visibility-row').classList.toggle('hidden', state.kind !== 'note');
  clearPendingFiles();
  $('#content').focus();
}

function setStatusFilter(status, button) {
  state.status = status;
  state.kindFilter = 'issue';
  document.querySelectorAll('[data-filter-status]').forEach((item) => {
    const active = button ? item === button : item.dataset.filterStatus === status;
    item.classList.toggle('active', active);
    item.setAttribute('aria-pressed', String(active));
  });
  $('#status-filter').value = status;
  setPage('issues');
}

document.querySelectorAll('.nav-item').forEach((button) => button.addEventListener('click', () => setPage(button.dataset.page)));
document.querySelectorAll('[data-go-page]').forEach((button) => button.addEventListener('click', () => setPage(button.dataset.goPage)));
$('#top-new-record').addEventListener('click', () => openCapture($('#top-new-record').dataset.captureKind || 'issue'));
document.addEventListener('click', (event) => {
  if (event.target.closest('[data-new-note]')) { openCapture('note'); return; }
  if (event.target.closest('[data-new-issue]')) { openCapture('issue'); return; }
  if (event.target.closest('[data-go-triage]')) {
    setStatusFilter('triage');
  }
});
syncTopNewRecord(state.page);
$('#close-capture').addEventListener('click', () => $('#capture').classList.add('hidden'));
document.querySelectorAll('[data-close-capture]').forEach((element) => element.addEventListener('click', () => $('#capture').classList.add('hidden')));
document.querySelectorAll('[data-layout]').forEach((button) => button.addEventListener('click', () => {
  state.layout = button.dataset.layout;
  document.querySelectorAll('[data-layout]').forEach((item) => item.classList.toggle('selected', item === button));
  loadRecords();
}));
$('#status-filter').addEventListener('change', (event) => { state.status = event.target.value; loadRecords(); });
[
  ['issues-project-filter', 'issue', 'project'],
  ['issues-work-type-filter', 'issue', 'workType'],
  ['notes-project-filter', 'note', 'project'],
  ['notes-work-type-filter', 'note', 'workType'],
].forEach(([id, kind, key]) => {
  const element = $(`#${id}`);
  if (!element) return;
  element.addEventListener('change', (event) => {
    state.filters[kind][key] = event.target.value;
    if (state.kindFilter === kind) loadRecords();
  });
});
document.querySelectorAll('[data-note-filter]').forEach((button) => button.addEventListener('click', () => {
  state.noteVisibility = button.dataset.noteFilter;
  document.querySelectorAll('[data-note-filter]').forEach((item) => {
    const selected = item === button;
    item.classList.toggle('selected', selected);
    item.setAttribute('aria-pressed', String(selected));
  });
  loadRecords();
}));
document.querySelectorAll('[data-kind]').forEach((button) => button.addEventListener('click', () => {
  state.kind = button.dataset.kind;
  $('#note-visibility-row').classList.toggle('hidden', state.kind !== 'note');
  $('#capture-title').textContent = state.kind === 'note' ? '新建随手记' : '新建问题';
  $('#content').placeholder = state.kind === 'note' ? '记下想法、资料、临时发现或待办…' : '记录问题现象、处理过程和验证结果…';
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
$('#close-detail').addEventListener('click', closeDetail);
$('#detail-backdrop').addEventListener('click', closeDetail);
  $('#user-switch').addEventListener('click', () => showUserLogin(true));
$('#user-login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const username = $('#username-input').value.normalize('NFKC').trim();
  if (!username || username.length > 80 || /[\u0000-\u001f]/.test(username)) return;
  localStorage.setItem('teamai-username', username);
  state.selected = null;
  closeDetail();
  await refreshMe();
  updateUserControl();
  const requestedRecord = new URLSearchParams(location.hash.slice(1)).get('record');
  if (requestedRecord && /^\d+$/.test(requestedRecord)) {
    document.body.classList.add('reader-mode');
    $('#reader-topbar').classList.remove('hidden');
    openDetail(requestedRecord);
  } else load();
});
$('#cancel-review')?.addEventListener('click', () => $('#review-editor').classList.add('hidden'));
$('#publish-knowledge')?.addEventListener('click', async () => {
  const issueId = $('#review-issue-id').value;
  const title = $('#review-title').value.trim();
  const content = $('#review-content').value.trim();
  if (!issueId || !title || !content) { message('请填写知识标题和正文。', true); return; }
  const button = $('#publish-knowledge');
  button.disabled = true;
  try {
    await jsonRequest(`/v1/issues/${issueId}/publish-knowledge`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, content, reviewer: currentUser() }),
    });
    message('知识已发布。');
    $('#review-editor').classList.add('hidden');
    await Promise.all([loadReviewQueue(), loadSummary()]);
  } catch (error) { message(`发布失败：${error.message}`, true); }
  finally { button.disabled = false; }
});
const syncRetryHandler = typeof createSyncRetryHandler === 'function'
  ? createSyncRetryHandler({
    request: jsonRequest,
    message,
    refresh: async () => { await refreshSyncStatus(); await loadSummary(); },
  })
  : null;
['#overview-sync-retry', '#sync-retry'].forEach((selector) => {
  const button = $(selector);
  if (button && syncRetryHandler) button.addEventListener('click', syncRetryHandler);
});
$('#capture-attachment-input').addEventListener('change', (event) => {
  addPendingFiles(event.target.files || []);
  event.target.value = '';
});
$('#capture-attachment-list').addEventListener('click', (event) => {
  const button = event.target.closest('[data-remove-pending]');
  if (!button) return;
  const index = Number(button.dataset.removePending);
  if (!Number.isInteger(index)) return;
  state.pendingFiles.splice(index, 1);
  renderPendingAttachments();
});
$('#attachment-input').addEventListener('change', async (event) => {
  const files = [...(event.target.files || [])];
  event.target.value = '';
  await uploadFilesToSelected(files);
});
$('#delete-record').addEventListener('click', async () => {
  if (!state.selected) return;
  const kind = state.selected.kind;
  if (kind !== 'note' && kind !== 'issue') return;
  if (kind === 'note') {
    const ownsNote = Boolean(currentUser()) && state.selected.owner_username === currentUser().trim().toLocaleLowerCase();
    if (!ownsNote) return;
  } else if (!currentUser()) {
    return;
  }
  const label = kind === 'note' ? '随手记' : '问题';
  if (!window.confirm(`确定删除${label}“${displayTitle(state.selected.title)}”及其附件吗？此操作不可恢复。`)) return;
  try {
    const endpoint = kind === 'note' ? `/v1/notes/${state.selected.id}` : `/v1/issues/${state.selected.id}`;
    await jsonRequest(endpoint, { method: 'DELETE' });
    const deletedId = state.selected.id;
    closeDetail();
    message(`${label}已删除。`);
    await Promise.all([load(), loadFilterOptions(kind)]);
    restoreResultsFocus(String(deletedId));
  } catch (error) { message(`删除失败：${error.message}`, true); }
});
$('#detail-meta').addEventListener('click', (event) => {
  const parent = event.target.closest('[data-open-parent]');
  if (!parent) return;
  openDetail(parent.dataset.openParent);
});
$('#attachment-list').addEventListener('click', async (event) => {
  const previewButton = event.target.closest('[data-preview-attachment]');
  if (previewButton) {
    event.preventDefault();
    await openAttachmentPreview({
      recordId: previewButton.dataset.recordId,
      attachmentId: previewButton.dataset.attachmentId,
      fileName: previewButton.dataset.filename,
      contentType: previewButton.dataset.contentType,
    });
    return;
  }
  const deleteButton = event.target.closest('[data-delete-attachment]');
  if (deleteButton) {
    event.preventDefault();
    if (!window.confirm(`确定删除附件“${deleteButton.dataset.filename}”吗？`)) return;
    try {
      await jsonRequest(`/v1/records/${deleteButton.dataset.recordId}/attachments/${deleteButton.dataset.attachmentId}`, { method: 'DELETE' });
      message('附件已删除。');
      await openDetail(deleteButton.dataset.recordId);
    } catch (error) { message(`附件删除失败：${error.message}`, true); }
    return;
  }
  const downloadButton = event.target.closest('[data-download-attachment]');
  if (downloadButton) {
    event.preventDefault();
    try {
      const { blob } = await fetchAttachmentBlob(downloadButton.dataset.recordId, downloadButton.dataset.attachmentId);
      downloadBlob(blob, downloadButton.dataset.filename);
    } catch (error) { message(`附件下载失败：${error.message}`, true); }
    return;
  }
  const link = event.target.closest('a[data-attachment-url]');
  if (!link) return;
  event.preventDefault();
  const kind = link.dataset.previewKind || attachmentPreviewKind(link.dataset.filename, link.dataset.contentType);
  if (kind !== 'other') {
    await openAttachmentPreview({
      recordId: link.dataset.recordId,
      attachmentId: link.dataset.attachmentId,
      fileName: link.dataset.filename,
      contentType: link.dataset.contentType,
    });
    return;
  }
  try {
    const { blob } = await fetchAttachmentBlob(link.dataset.recordId, link.dataset.attachmentId);
    downloadBlob(blob, link.dataset.filename);
  } catch (error) { message(`附件下载失败：${error.message}`, true); }
});
$('#close-attachment-preview').addEventListener('click', closeAttachmentPreview);
document.querySelectorAll('[data-close-preview]').forEach((element) => element.addEventListener('click', closeAttachmentPreview));
$('#attachment-preview-download').addEventListener('click', async () => {
  if (!state.previewDownload) return;
  try {
    const { blob } = await fetchAttachmentBlob(state.previewDownload.recordId, state.previewDownload.attachmentId);
    downloadBlob(blob, state.previewDownload.fileName);
  } catch (error) { message(`附件下载失败：${error.message}`, true); }
});
$('#attachment-preview-body').addEventListener('click', async (event) => {
  if (!event.target.closest('[data-preview-download-now]') || !state.previewDownload) return;
  try {
    const { blob } = await fetchAttachmentBlob(state.previewDownload.recordId, state.previewDownload.attachmentId);
    downloadBlob(blob, state.previewDownload.fileName);
  } catch (error) { message(`附件下载失败：${error.message}`, true); }
});
document.addEventListener('paste', (event) => {
  const files = clipboardFiles(event);
  if (!files.length) return;
  const captureOpen = !$('#capture').classList.contains('hidden');
  const detailOpen = !$('#detail').classList.contains('hidden');
  if (captureOpen) {
    addPendingFiles(files);
    return;
  }
  if (detailOpen && canUploadToSelected()) {
    event.preventDefault();
    uploadFilesToSelected(files);
  }
});
$('#edit-record').addEventListener('click', () => {
  if (!state.selected) return;
  $('#detail-content').classList.add('hidden');
  $('#record-editor').classList.remove('hidden');
  $('#edit-title').focus();
});
$('#cancel-edit').addEventListener('click', () => {
  $('#record-editor').classList.add('hidden');
  $('#detail-content').classList.remove('hidden');
  $('#edit-record').focus();
});
$('#save-edit').addEventListener('click', async () => {
  if (!state.selected) return;
  if (!currentUser()) { message('请先设置用户名后再编辑。', true); return; }
  const title = $('#edit-title').value.trim();
  const content = $('#edit-content').value.trim();
  if (!title || !content) { message('标题和正文不能为空。', true); return; }
  if (!$('#edit-work-type').value) { message('请选择所属类型。', true); $('#edit-work-type').focus(); return; }
  const button = $('#save-edit');
  button.disabled = true;
  try {
    const userTags = (state.selected.tags || []).filter((tag) => !String(tag).startsWith('source::') && !String(tag).startsWith('type::'));
    const record = await jsonRequest(`/v1/records/${state.selected.id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_updated_at: state.selected.updated_at,
        title, content, project: $('#edit-project').value.trim(),
        work_type: $('#edit-work-type').value,
        tags: userTags,
        tech_stack: state.selected.tech_stack || [],
        ...(state.selected.kind === 'note' ? { visibility: $('#edit-visibility').value } : {}),
      }),
    });
    message(record.sync_error ? '修改已保存；GitLab 同步待处理。' : '修改已保存。', Boolean(record.sync_error && record.sync_error !== 'GitLab synchronization pending'));
    await openDetail(record.id);
    await load();
    if (record.kind === 'issue') setPage('issues');
  } catch (error) { message(error.message, true); }
  finally { button.disabled = false; }
});


$('#submit').addEventListener('click', async () => {
  const content = $('#content').value.trim();
  if (!content) { message('请先填写内容。', true); $('#content').focus(); return; }
  const workType = $('#work-type').value;
  if (!workType) { message('请选择所属类型。', true); $('#work-type').focus(); return; }
  const button = $('#submit');
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  message('正在整理并保存…');
  try {
    const endpoint = { note: '/v1/notes', issue: '/v1/issues' }[state.kind];
    const pending = [...state.pendingFiles];
    const record = await jsonRequest(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: $('#title').value.trim() || null, content, project: $('#project').value.trim(), work_type: workType, ...(state.kind === 'note' ? { visibility: $('#note-visibility').value } : {}) }) });
    let uploadError = '';
    let uploaded = 0;
    if (!record.duplicate && pending.length) {
      for (const file of pending) {
        try {
          await uploadAttachmentFile(record.id, file);
          uploaded += 1;
        } catch (error) {
          uploadError = error.message;
          break;
        }
      }
    }
    const baseMessage = record.duplicate ? '这条内容已经存在。' : record.kind === 'note' && record.visibility === 'private' ? '已保存到个人视图。用户名免密，不具备身份验证。' : record.sync_error ? `已保存到本地；GitLab 同步待处理：${record.sync_error}` : '已保存，记录已进入团队库。';
    if (uploadError) message(`${baseMessage} 附件上传中断：${uploadError}`, true);
    else if (uploaded) message(`${baseMessage} 已上传 ${uploaded} 个附件。`, Boolean(record.sync_error && record.sync_error !== 'GitLab synchronization pending'));
    else message(baseMessage, Boolean(record.sync_error && record.sync_error !== 'GitLab synchronization pending'));
    $('#capture').classList.add('hidden');
    if (!record.duplicate) { $('#title').value = ''; $('#content').value = ''; $('#project').value = ''; $('#work-type').value = ''; clearPendingFiles(); }
    await load();
    if (record.kind === 'issue') setPage('issues'); else setPage('notes');
    if (!record.duplicate && (uploaded || pending.length)) await openDetail(record.id);
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
  const verification = $('#verification-input').value.trim();
  if (path === 'resolve' && !verification) { message('请补充验证方法和结果后再标记已解决。', true); $('#verification-input').focus(); return; }
  const buttons = ['#append-solution', '#resolve-issue', '#reopen-issue'].map($);
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const record = await jsonRequest(`/v1/issues/${state.selected.id}/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ solution, source: 'web', ...(path === 'resolve' ? { verification } : {}) }) });
    const pending = record.sync_error === 'GitLab synchronization pending';
    const okText = path === 'resolve' ? '问题已解决。' : '记录已更新。';
    message(record.sync_error ? (pending ? `${okText} GitLab 同步正在排队。` : `${okText} GitLab 同步失败：${record.sync_error}`) : okText, Boolean(record.sync_error && !pending));
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
  if (event.key === 'Escape') {
    if (!$('#attachment-preview')?.classList.contains('hidden')) {
      closeAttachmentPreview();
      return;
    }
    if (!$('#capture').classList.contains('hidden')) {
      $('#capture').classList.add('hidden');
      return;
    }
    closeDetail();
  }
});
renderPendingAttachments();
updateUserControl();
applyAdminVisibility();
const readerRecordId = new URLSearchParams(location.hash.slice(1)).get('record');
if (currentUser()) {
  if (readerRecordId && /^\d+$/.test(readerRecordId)) {
    document.body.classList.add('reader-mode');
    $('#reader-topbar').classList.remove('hidden');
    refreshMe().then(() => { updateUserControl(); openDetail(readerRecordId); });
  } else load();
} else showUserLogin();
