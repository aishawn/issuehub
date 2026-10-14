const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'web/styles.css'), 'utf8');

function colorToken(name) {
  const match = css.match(new RegExp(`${name}:(#[0-9a-f]{6})`, 'i'));
  assert.ok(match, `Missing CSS color token: ${name}`);
  return match[1];
}

function relativeLuminance(hex) {
  const channels = [1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16) / 255);
  const linear = channels.map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrast(foreground, background) {
  const values = [relativeLuminance(foreground), relativeLuminance(background)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

function cssRule(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const rule = css.match(new RegExp(`${escaped}\\{([^}]*)\\}`));
  assert.ok(rule, `Missing CSS rule: ${selector}`);
  return rule[1];
}

test('status filter buttons expose their initial pressed state', () => {
  const buttons = [...html.matchAll(/<button\b[^>]*data-filter-status[^>]*>/g)].map(([tag]) => tag);
  const states = buttons.map((tag) => tag.match(/aria-pressed="(true|false)"/)?.[1]);

  assert.equal(buttons.length, 4);
  assert.deepEqual(states, ['true', 'false', 'false', 'false']);
});

test('muted text meets WCAG AA contrast on the application surfaces', () => {
  const muted = colorToken('--muted');
  const paper = colorToken('--paper');
  const overview = css.match(/\.overview\{background:(#[0-9a-f]{6})/i)?.[1];
  const tips = css.match(/\.tips\{[^}]*background:(#[0-9a-f]{6})/i)?.[1];
  assert.ok(overview, 'Missing overview surface color');
  assert.ok(tips, 'Missing tips surface color');

  for (const background of [paper, '#ffffff', overview, tips]) {
    assert.ok(contrast(muted, background) >= 4.5, `${muted} on ${background} is below 4.5:1`);
  }
});

test('primary touch controls meet the 44px minimum target size', () => {
  for (const selector of ['.segmented button', '.filters select', '.sync-retry']) {
    const minimum = cssRule(selector).match(/min-height:\s*(\d+)px/);
    assert.ok(minimum && Number(minimum[1]) >= 44, `${selector} is smaller than 44px`);
  }
  const closeButton = cssRule('.ghost');
  assert.ok(Number(closeButton.match(/width:\s*(\d+)px/)?.[1]) >= 44);
  assert.ok(Number(closeButton.match(/height:\s*(\d+)px/)?.[1]) >= 44);
});
