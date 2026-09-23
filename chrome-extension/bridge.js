(() => {
  const allowed = new Set(["status", "following", "favorites", "listItems", "readItem"]);
  const markReady = () => {
    const guide = document.getElementById("extension-guide");
    if (guide && !document.getElementById("extension-ready")) {
      const badge = document.createElement("strong");
      badge.id = "extension-ready";
      badge.textContent = " · 扩展已就绪";
      guide.append(badge);
    }
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", markReady, {once:true});
  else markReady();
  window.addEventListener("message", event => {
    if (event.data?.source === "xianyu-report-ui") {
      const badge = document.getElementById("extension-ready");
      if (badge) badge.textContent = " · 扩展收到页面请求";
    }
    if (event.origin !== location.origin) return;
    const request = event.data;
    if (!request || request.source !== "xianyu-report-ui" || !allowed.has(request.command) || typeof request.id !== "string") return;
    chrome.runtime.sendMessage({command: request.command, payload: request.payload || {}}, response => {
      const error = chrome.runtime.lastError?.message;
      const badge = document.getElementById("extension-ready");
      if (badge) badge.textContent = error ? " · 扩展连接失败" : " · 扩展已响应";
      window.postMessage({
        source: "xianyu-report-extension", id: request.id,
        result: error ? {error} : response || {error: "扩展没有返回结果"}
      }, location.origin);
    });
  });
})();
