(function (global) {
  function createDetailFocus(fallback) {
    let opener = null;

    return {
      open(trigger, heading) {
        if (opener === null) opener = trigger;
        heading.focus({ preventScroll: true });
      },
      close() {
        const target = opener?.isConnected ? opener : (typeof fallback === 'function' ? fallback() : fallback);
        opener = null;
        target.focus();
      },
    };
  }

  global.createDetailFocus = createDetailFocus;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { createDetailFocus };
  }
})(globalThis);
