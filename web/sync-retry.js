(function (global) {
  function createSyncRetryHandler({ request, message, refresh }) {
    return async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = '同步中…';
      try {
        const result = await request('/v1/sync', { method: 'POST' });
        message(`同步完成：成功 ${result.synced} 条，失败 ${result.failed} 条，剩余 ${result.pending} 条。`, result.failed > 0);
        await refresh();
      } catch (error) {
        message(`同步请求失败：${error.message}`, true);
      } finally {
        button.disabled = false;
        button.textContent = '重试同步';
      }
    };
  }

  global.createSyncRetryHandler = createSyncRetryHandler;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { createSyncRetryHandler };
  }
})(globalThis);
