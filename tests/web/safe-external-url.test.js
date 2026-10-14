const assert = require('node:assert/strict');
const test = require('node:test');
const { safeExternalUrl } = require('../../web/safe-external-url.js');

test('accepts absolute HTTP and HTTPS links without credentials', () => {
  assert.equal(safeExternalUrl('https://gitlab.example/group/project/-/issues/7'), 'https://gitlab.example/group/project/-/issues/7');
  assert.equal(safeExternalUrl('http://gitlab.local/issues/7'), 'http://gitlab.local/issues/7');
});

test('rejects executable schemes, relative URLs, and embedded credentials', () => {
  for (const value of ['javascript:alert(1)', 'data:text/html,hello', '/issues/7', 'https://user:secret@gitlab.example/issues/7', ' https://gitlab.example/issues/7']) {
    assert.equal(safeExternalUrl(value), null, value);
  }
});
