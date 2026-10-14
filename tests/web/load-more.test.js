const assert = require('node:assert/strict');
const test = require('node:test');
const { createLoadMoreHandler } = require('../../web/load-more.js');

function setup(load) {
  const button = { disabled: false, textContent: '加载更多' };
  const handler = createLoadMoreHandler(load);
  return { button, click: () => handler({ currentTarget: button }) };
}

test('load more shows progress and restores its normal state after success', async () => {
  const state = setup(async () => {
    assert.equal(state.button.disabled, true);
    assert.equal(state.button.textContent, '正在加载…');
    return true;
  });

  await state.click();

  assert.equal(state.button.disabled, false);
  assert.equal(state.button.textContent, '加载更多');
});

test('load more stays retryable after a failed request', async () => {
  const state = setup(async () => false);

  await state.click();

  assert.equal(state.button.disabled, false);
  assert.equal(state.button.textContent, '加载失败，重试');
});

test('load more restores the button if its handler unexpectedly rejects', async () => {
  const state = setup(async () => { throw new Error('unexpected failure'); });

  await state.click();

  assert.equal(state.button.disabled, false);
  assert.equal(state.button.textContent, '加载失败，重试');
});
