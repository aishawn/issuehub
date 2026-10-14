const assert = require('node:assert/strict');
const test = require('node:test');
const { createDetailFocus } = require('../../web/detail-focus.js');

function focusable(isConnected = true) {
  return { isConnected, calls: [], focus(options) { this.calls.push(options); } };
}

test('detail focus moves to its heading and returns to the trigger', () => {
  const search = focusable();
  const trigger = focusable();
  const heading = focusable();
  const detail = createDetailFocus(search);

  detail.open(trigger, heading);
  detail.close();

  assert.deepEqual(heading.calls, [{ preventScroll: true }]);
  assert.deepEqual(trigger.calls, [undefined]);
  assert.deepEqual(search.calls, []);
});

test('detail focus falls back to search if its trigger left the results', () => {
  const search = focusable();
  const trigger = focusable(false);
  const detail = createDetailFocus(search);

  detail.open(trigger, focusable());
  detail.close();

  assert.deepEqual(search.calls, [undefined]);
});

test('refreshing an open detail keeps the original list trigger for close', () => {
  const search = focusable();
  const trigger = focusable();
  const actionButton = focusable();
  const heading = focusable();
  const detail = createDetailFocus(search);

  detail.open(trigger, heading);
  detail.open(actionButton, heading);
  detail.close();

  assert.deepEqual(trigger.calls, [undefined]);
  assert.deepEqual(actionButton.calls, []);
});

test('detail close resolves the refreshed record trigger before falling back', () => {
  const search = focusable();
  const refreshedTrigger = focusable();
  const trigger = focusable(false);
  const detail = createDetailFocus(() => refreshedTrigger || search);

  detail.open(trigger, focusable());
  detail.close();

  assert.deepEqual(refreshedTrigger.calls, [undefined]);
  assert.deepEqual(search.calls, []);
});
