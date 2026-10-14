(function (global) {
  function safeExternalUrl(value) {
    if (typeof value !== 'string' || value.trim() !== value || !value) return null;
    try {
      const url = new URL(value);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null;
      return url.href;
    } catch {
      return null;
    }
  }

  global.safeExternalUrl = safeExternalUrl;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { safeExternalUrl };
  }
})(globalThis);
