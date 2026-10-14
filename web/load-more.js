(function (global) {
  function createLoadMoreHandler(load) {
    return async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = '正在加载…';
      let loaded = false;
      try {
        loaded = await load();
      } catch {
        loaded = false;
      } finally {
        button.disabled = false;
        button.textContent = loaded ? '加载更多' : '加载失败，重试';
      }
    };
  }

  global.createLoadMoreHandler = createLoadMoreHandler;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { createLoadMoreHandler };
  }
})(globalThis);
