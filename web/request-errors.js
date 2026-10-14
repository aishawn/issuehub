(function (global) {
  const fields = {
    content: '内容', title: '标题', source: '来源', tags: '标签',
    environment: '环境', reproduction: '复现步骤', solution: '解决方案',
    author_id: '作者', session_id: '会话 ID', q: '搜索关键词',
    kind: '记录类型', status: '状态', limit: '每页数量', offset: '页码偏移',
  };
  const validationMessages = {
    missing: '为必填项',
    string_too_short: '内容不能为空',
    string_too_long: '超过允许长度',
    list_too_long: '项目数量超过限制',
    greater_than_equal: '数值低于允许范围',
    less_than_equal: '数值超过允许范围',
  };

  function formatApiError(detail, status) {
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      const messages = detail.map((issue) => {
        if (typeof issue === 'string') return issue;
        if (!issue || typeof issue !== 'object') return '';
        const location = Array.isArray(issue.loc) ? issue.loc.filter((part) => !['body', 'query', 'path'].includes(part)) : [];
        const field = location.map((part) => typeof part === 'number' ? `第 ${part + 1} 项` : (fields[part] || String(part))).join(' · ');
        const message = validationMessages[issue.type] || issue.msg || '输入内容不符合要求';
        return field ? `${field}：${message}` : message;
      }).filter(Boolean);
      if (messages.length) return messages.join('；');
    }
    return `请求失败（${status}）`;
  }

  global.formatApiError = formatApiError;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { formatApiError };
  }
})(globalThis);
