const assert = require('node:assert/strict');
const test = require('node:test');
const { createSyncRetryHandler } = require('../../web/sync-retry.js');

function setup(request) {
  const calls = [];
  const button = { disabled: false, textContent: '重试同步' };
  const handler = createSyncRetryHandler({
    request,
    message: (...args) => calls.push(['message', ...args]),
    refresh: async () => calls.push(['refresh']),
  });
  return { button, calls, click: () => handler({ currentTarget: button }) };
}

test('successful retry reports counts, refreshes the page, and restores the button', async () => {
  const state = setup(async (path, options) => {
    assert.equal(path, '/v1/sync');
    assert.deepEqual(options, { method: 'POST' });
    return { synced: 2, failed: 1, pending: 3 };
  });

  await state.click();

  assert.deepEqual(state.calls, [
    ['message', '同步完成：成功 2 条，失败 1 条，剩余 3 条。', true],
    ['refresh'],
  ]);
  assert.equal(state.button.disabled, false);
  assert.equal(state.button.textContent, '重试同步');
});

test('failed retry reports the error and restores the button without refreshing', async () => {
  const state = setup(async () => { throw new Error('temporary failure'); });

  await state.click();

  assert.deepEqual(state.calls, [['message', '同步请求失败：temporary failure', true]]);
  assert.equal(state.button.disabled, false);
  assert.equal(state.button.textContent, '重试同步');
});
