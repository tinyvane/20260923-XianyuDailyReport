(() => {
  const allowed = new Set(["status", "following", "favorites", "listItems", "readItem"]);
  window.addEventListener("message", event => {
    if (event.source !== window || event.origin !== location.origin) return;
    const request = event.data;
    if (!request || request.source !== "xianyu-report-ui" || !allowed.has(request.command) || typeof request.id !== "string") return;
    chrome.runtime.sendMessage({command: request.command, payload: request.payload || {}}, response => {
      const error = chrome.runtime.lastError?.message;
      window.postMessage({
        source: "xianyu-report-extension", id: request.id,
        result: error ? {error} : response || {error: "扩展没有返回结果"}
      }, location.origin);
    });
  });
})();
