const assert = require('node:assert/strict');
const test = require('node:test');
const { formatApiError } = require('../../web/request-errors.js');

test('validation errors name the field and explain the limit in Chinese', () => {
  const message = formatApiError([
    { loc: ['body', 'title'], type: 'string_too_long', msg: 'String should have at most 180 characters' },
    { loc: ['body', 'tags', 1], type: 'string_too_short', msg: 'String should have at least 1 character' },
  ], 422);

  assert.equal(message, '标题：超过允许长度；标签 · 第 2 项：内容不能为空');
});

test('preserves readable API errors and provides a status fallback', () => {
  assert.equal(formatApiError('Authentication required', 401), 'Authentication required');
  assert.equal(formatApiError([{ loc: ['query', 'limit'], type: 'greater_than_equal' }], 422), '每页数量：数值低于允许范围');
  assert.equal(formatApiError(null, 503), '请求失败（503）');
});

test('formats unknown validation fields without exposing an object placeholder', () => {
  assert.equal(formatApiError([{ loc: ['body', 'custom'], msg: 'Invalid value' }], 422), 'custom：Invalid value');
});
