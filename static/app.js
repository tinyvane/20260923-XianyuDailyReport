const $ = id => document.getElementById(id);
let current = null;
let toastTimer;
let collecting = false;
let ownerResolving = "";
let ownerQueue = Promise.resolve();
let autoAttempted = false;
let lastAutomaticAttempt = 0;
const esc = text => String(text ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const number = n => n == null ? "—" : (n > 0 ? "+" : "") + n.toLocaleString("zh-CN");
const shownFavorites = favorites => {
  const query = $("favorite-search").value.trim().toLowerCase();
  const state = $("favorite-state-filter").value;
  return favorites.filter(f => (!state || (state === "已选" ? f.selected : (f.state || "待采集") === state)) &&
    (!query || `${f.id} ${f.title} ${f.observed_title || ""}`.toLowerCase().includes(query)));
};
const notify = message => { $("toast").textContent = message; $("toast").classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(()=>$("toast").classList.remove("show"),4500); };
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: {"Content-Type":"application/json", ...(options.headers || {})} });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `请求失败：${response.status}`);
  return data;
}
async function action(path, options, success) {
  try { const result = await api(path, options); if (success) notify(typeof success === "function" ? success(result) : success); await refresh(); return result; }
  catch(error) { notify(error.message); return null; }
}
function render(data) {
  current = data;
  $("browser-mode").value = data.settings.browser_mode || "edge_extension";
  const isExtension = $("browser-mode").value !== "edge";
  $("open-browser").textContent = isExtension ? "连接现有浏览器" : "打开固定 Edge";
  $("import-following").textContent = "导入收藏商品";
  $("extension-guide").hidden = !isExtension;
  $("report-date").textContent = data.date;
  $("hours").value = String(data.hours);
  $("interval-label").textContent = data.hours;
  const favorites = data.favorites || [];
  const selectedFavorites = favorites.filter(f => f.selected);
  const hasScope = selectedFavorites.length || data.sellers.some(s => s.selected);
  $("valid-count").textContent = data.items.length;
  const state = !hasScope ? "等待选择重点" : data.items.length ? "已有采集数据" : "等待采集";
  $("data-state").textContent = state;
  $("data-state").classList.toggle("warn", state !== "已有采集数据");
  $("latest-run").textContent = !hasScope ? `已导入 ${favorites.length} 件候选，请在下方勾选重点商品` : data.run ? `最近采集：${new Date(data.run.finished_at || data.run.started_at).toLocaleString("zh-CN")} · ${data.run.message}` : "尚无采集记录";
  $("views-total").textContent = number(data.views_delta);
  $("wants-total").textContent = number(data.wants_delta);
  $("count-unknown").textContent = data.count_anomalies;
  $("price-change").textContent = data.price_changes == null ? "—" : data.price_changes;
  $("missing-count").textContent = data.uncomparable;
  const visibleFavorites = shownFavorites(favorites);
  if (!collecting) $("control-status").textContent = ownerResolving || `闲鱼登录：${data.login} · 已选 ${selectedFavorites.length} 件商品、关联 ${data.sellers.filter(s=>s.linked).length} 位卖家`;
  $("collect").disabled = collecting || Boolean(ownerResolving);
  $("collect-pending").disabled = collecting || Boolean(ownerResolving) || !favorites.some(f => f.selected && (f.state || "待采集") === "待采集");
  $("ranking-note").textContent = data.top.length ? `按最近 ${data.hours} 小时浏览增长排序` : "";
  $("ranking").innerHTML = data.top.length ? data.top.map((item, i) => {
    const seller = data.sellers.find(s => s.id === item.seller_id);
    return `<div class="rank-row"><span class="rank">${i+1}</span><span class="rank-gray">${i+1}</span>${item.image ? `<img src="${esc(item.image)}" alt="" referrerpolicy="no-referrer">` : '<span class="avatar-placeholder">▣</span>'}<a class="name" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer" title="${esc(item.title)}">${esc(item.title)}</a><span class="seller-name">${esc(seller?.name || "未知卖家")}</span><span class="deltas"><b class="view">${number(item.views_delta)}浏览</b>，<b class="want">${number(item.wants_delta)}想要</b></span></div>`;
  }).join("") : '<div class="empty-state">还没有可对比的数据。完成首次采集后，经过选定时长再采集即可看到增长榜。</div>';
  const observedSellers = data.sellers.filter(s => s.linked || s.selected);
  $("seller-note").textContent = observedSellers.length ? `${observedSellers.length} 位重点卖家` : "";
  $("seller-report").innerHTML = observedSellers.length ? observedSellers.map(s => `<div class="seller-bullet"><span><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.name)}</a>${s.linked ? '<small class="candidate-label">重点商品卖家</small>' : ""}：已采集${s.count}件范围内商品，合计${number(s.views_delta)}浏览、${number(s.wants_delta)}想要</span></div>`).join("") : '<div class="empty-state">先在下方勾选重点商品；详情识别成功后，卖家会自动出现在这里。</div>';
  $("favorite-note").textContent = favorites.length ? `已选 ${selectedFavorites.length} / 导入 ${favorites.length} 件 · 当前显示 ${visibleFavorites.length} 件` : "尚未导入";
  const favoriteScroll = $("favorite-list").scrollTop;
  $("favorite-list").innerHTML = visibleFavorites.length ? visibleFavorites.map(f => `<label class="favorite-chip"><input type="checkbox" data-favorite="${esc(f.id)}" ${f.selected ? "checked" : ""}><span class="favorite-title"><a href="${esc(f.url)}" target="_blank" rel="noopener noreferrer">${esc(f.observed_title || f.title)}</a><small title="${esc(f.last_error || "")}">${esc(f.state || "待采集")}${f.last_checked ? ` · ${esc(new Date(f.last_checked).toLocaleString("zh-CN"))}` : ""}</small></span><span class="favorite-price">${f.price || f.card_price ? `¥${esc(f.price || f.card_price)}` : "—"}</span></label>`).join("") : `<p class="list-empty">${favorites.length ? "当前筛选没有商品" : "先在闲鱼打开“我的收藏”有效宝贝页，再点击“导入收藏商品”。"}</p>`;
  $("favorite-list").scrollTop = favoriteScroll;
  $("favorite-select-visible").disabled = !visibleFavorites.length;
  $("favorite-clear-visible").disabled = !visibleFavorites.length;
  $("seller-list").innerHTML = data.sellers.length ? data.sellers.map(s => `<div class="seller-chip"><input type="checkbox" data-select="${esc(s.id)}" aria-label="监控 ${esc(s.name)} 的其他商品" ${s.selected ? "checked" : ""}><input class="chip-name" data-name="${esc(s.id)}" aria-label="卖家备注" value="${esc(s.name)}">${s.linked ? '<small class="candidate-label">已关联</small>' : ""}<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">主页 ↗</a><button type="button" data-remove="${esc(s.id)}" aria-label="取消监控 ${esc(s.name)}">×</button></div>`).join("") : '<p class="list-empty">暂无卖家</p>';
  $("auto-collect").checked = data.settings.auto_collect;
  $("schedule-minutes").value = String(data.settings.schedule_minutes);
}
async function refresh() {
  try { const path = current ? `/api/report?hours=${encodeURIComponent($("hours").value || 24)}` : "/api/report"; render(await api(path)); }
  catch(error) { notify(error.message); }
}
function extensionCall(command, payload = {}) {
  return new Promise((resolve, reject) => {
    const id = crypto.randomUUID();
    const timer = setTimeout(() => {
      window.removeEventListener("message", receive);
      reject(Error("未收到浏览器扩展响应。请在扩展管理页加载本项目 chrome-extension 文件夹，并刷新日报页面"));
    }, command === "status" ? 7000 : command === "favorites" ? 30000 : command === "following" ? 15000 : 55000);
    function receive(event) {
      if (event.origin !== location.origin || event.data?.source !== "xianyu-report-extension" || event.data.id !== id) return;
      clearTimeout(timer);
      window.removeEventListener("message", receive);
      event.data.result?.error ? reject(Error(event.data.result.error)) : resolve(event.data.result);
    }
    window.addEventListener("message", receive);
    window.postMessage({source:"xianyu-report-ui",id,command,payload},location.origin);
  });
}
async function chromeStatus() {
  const result = await extensionCall("status");
  await api("/api/chrome/status", {method:"POST",body:JSON.stringify({ok:result.ok})});
  await refresh();
  return result;
}
async function collectChrome({pendingOnly = false} = {}) {
  if (collecting) return;
  collecting = true;
  $("collect").disabled = true;
  $("control-status").textContent = "正在连接已登录的浏览器";
  let run = null, found = 0, valid = 0;
  const errors = [];
  try {
    await chromeStatus();
    const sellers = pendingOnly ? [] : current.sellers.filter(s => s.selected);
    const favorites = (current.favorites || []).filter(f => f.selected && (!pendingOnly || (f.state || "待采集") === "待采集"));
    if (!sellers.length && !favorites.length) throw Error(pendingOnly ? "没有待采集的重点商品" : "请先勾选重点商品或单独监控卖家其他商品");
    run = await api("/api/chrome/run/start", {method:"POST"});
    const scanned = new Set();
    let consecutiveFailures = 0;
    for (const favorite of favorites) {
      found++;
      scanned.add(favorite.id);
      try {
        $("control-status").textContent = `正在采集收藏商品 ${valid}/${found} 件有效`;
        const result = await extensionCall("readItem", {url:favorite.url});
        await api("/api/chrome/run/item", {method:"POST",body:JSON.stringify({
          ...result.item, seller_id:result.item.sellerId,
          seller_url:result.item.sellerUrl, seller_name:result.item.sellerName,
          url:favorite.url
        })});
        valid++;
        consecutiveFailures = 0;
      } catch(error) {
        errors.push(`收藏商品 ${favorite.id}：${error.message}`);
        const deleted = error.message.includes("商品已被删除");
        await api(`/api/favorites/${encodeURIComponent(favorite.id)}/failure`, {method:"POST",body:JSON.stringify({state:deleted ? "失效" : "读取失败",reason:error.message.slice(0,180)})}).catch(()=>{});
        consecutiveFailures = deleted ? 0 : consecutiveFailures + 1;
        if (!pendingOnly && consecutiveFailures >= 3) { errors.push("连续 3 件商品读取失败，已暂停本轮采集"); break; }
      } finally {
        await api("/api/chrome/run/progress", {method:"POST",body:JSON.stringify({run_id:run.run_id,found,valid})}).catch(()=>{});
      }
    }
    for (const seller of sellers) {
      if (consecutiveFailures >= 3) break;
      try {
        $("control-status").textContent = `正在读取 ${seller.name} 的商品`;
        const list = await extensionCall("listItems", {url:seller.url});
        if (!list.urls.length) { errors.push(`${seller.name}：未发现可识别商品`); continue; }
        for (const url of list.urls) {
          const itemId = new URL(url).searchParams.get("id");
          if (scanned.has(itemId)) continue;
          scanned.add(itemId);
          found++;
          try {
            $("control-status").textContent = `正在采集 ${seller.name} · ${valid}/${found} 件有效`;
            const result = await extensionCall("readItem", {url,sellerId:seller.id});
            await api("/api/chrome/run/item", {method:"POST",body:JSON.stringify({...result.item,seller_id:seller.id,seller_url:result.item.sellerUrl,seller_name:result.item.sellerName,url})});
            valid++;
            consecutiveFailures = 0;
          } catch(error) {
            errors.push(`${seller.name} / 商品：${error.message}`);
            consecutiveFailures = error.message.includes("商品已被删除") ? 0 : consecutiveFailures + 1;
            if (consecutiveFailures >= 3) { errors.push("连续 3 件商品读取失败，已暂停本轮采集"); break; }
          } finally {
            await api("/api/chrome/run/progress", {method:"POST",body:JSON.stringify({run_id:run.run_id,found,valid})}).catch(()=>{});
          }
        }
      } catch(error) { errors.push(`${seller.name}：${error.message}`); }
    }
    const finish = await api("/api/chrome/run/finish", {method:"POST",body:JSON.stringify({run_id:run.run_id,found,valid,errors})});
    notify(`${finish.state}：${valid}/${found} 件有效。 ${finish.message}`);
  } catch(error) {
    if (run) await api("/api/chrome/run/finish", {method:"POST",body:JSON.stringify({run_id:run.run_id,found,valid,errors:[...errors,error.message]})}).catch(()=>{});
    notify(error.message);
  } finally {
    collecting = false;
    $("collect").disabled = false;
    await refresh();
  }
}
async function collectEdge(pendingOnly = false) {
  if (collecting) return;
  collecting = true;
  $("collect").disabled = true;
  $("control-status").textContent = "正在复用已登录的 Edge 资料采集";
  try {
    const result = await api(`/api/edge/collect${pendingOnly ? "?pending_only=true" : ""}`, {method:"POST"});
    notify(`${result.state}：${result.valid}/${result.found} 件有效。 ${result.message}`);
  } catch(error) { notify(error.message); }
  finally { collecting = false; $("collect").disabled = false; await refresh(); }
}
async function collectSelected() {
  if (ownerResolving) { notify("正在识别所选商品的卖家，请稍候"); return; }
  lastAutomaticAttempt = Date.now();
  return $("browser-mode").value === "edge" ? collectEdge() : collectChrome();
}
function queueOwnerResolution(ids) {
  const unresolved = ids.filter(id => current?.favorites.some(f => f.id === id && f.selected && !f.seller_id));
  if (!unresolved.length) return;
  ownerQueue = ownerQueue.then(async () => {
    let consecutiveFailures = 0;
    for (const id of unresolved) {
      const favorite = current?.favorites.find(f => f.id === id);
      if (!favorite?.selected || favorite.seller_id) continue;
      ownerResolving = `正在识别重点商品 ${id} 的卖家`;
      render(current);
      try {
        let saved;
        if ($("browser-mode").value === "edge") {
          saved = await api(`/api/edge/favorites/${encodeURIComponent(id)}/resolve`, {method:"POST"});
        } else {
          const result = await extensionCall("readItem", {url:favorite.url});
          saved = await api("/api/chrome/run/item", {method:"POST",body:JSON.stringify({
            ...result.item, seller_id:result.item.sellerId,
            seller_url:result.item.sellerUrl, seller_name:result.item.sellerName,
            url:favorite.url
          })});
        }
        consecutiveFailures = 0;
        notify(`已关联重点卖家：${saved.seller_name}`);
      } catch (error) {
        const deleted = error.message.includes("商品已被删除") || error.message.includes("已被删除");
        await api(`/api/favorites/${encodeURIComponent(id)}/failure`, {method:"POST",body:JSON.stringify({
          state:deleted ? "失效" : "读取失败", reason:error.message.slice(0,180)
        })}).catch(()=>{});
        notify(`商品 ${id} 卖家识别失败：${error.message}`);
        consecutiveFailures = deleted ? 0 : consecutiveFailures + 1;
      }
      await refresh();
      if (consecutiveFailures >= 3) {
        notify("连续 3 件商品详情读取失败，已暂停自动识别；已勾选的商品仍保留");
        break;
      }
    }
    ownerResolving = "";
    await refresh();
  }).catch(error => { ownerResolving = ""; notify(error.message); refresh(); });
}
$("open-browser").addEventListener("click", async () => {
  if ($("browser-mode").value === "edge") {
    await action("/api/edge/connect", {method:"POST"}, "已打开原有 Edge 闲鱼窗口；登录资料会继续保留");
    return;
  }
  try { await chromeStatus(); notify("已连接当前浏览器的闲鱼标签页"); }
  catch(error) { notify(error.message); }
});
$("check-login").addEventListener("click", async () => {
  if ($("browser-mode").value === "edge") {
    await action("/api/edge/check", {method:"POST"}, result => result.ok ? "Edge 闲鱼登录有效" : "Edge 登录未通过，请在原窗口重新登录");
    return;
  }
  try { await chromeStatus(); notify("当前浏览器的闲鱼会话可用"); }
  catch(error) { await api("/api/chrome/status", {method:"POST",body:JSON.stringify({ok:false})}).catch(()=>{}); notify(error.message); await refresh(); }
});
$("import-following").addEventListener("click", async () => {
  if ($("browser-mode").value === "edge") {
    await action("/api/edge/favorites", {method:"POST"}, result => `识别 ${result.recognized} 件收藏商品，新增 ${result.imported} 件${result.limited ? "；已达到单次 200 件上限" : ""}`);
    return;
  }
  try {
    await chromeStatus();
    const result = await extensionCall("favorites");
    const saved = await api("/api/chrome/favorites", {method:"POST",body:JSON.stringify(result.candidates)});
    notify(`识别 ${saved.recognized} 件收藏商品，新增 ${saved.imported} 件${saved.limited ? "；已达到单次 200 件上限" : ""}`);
    await refresh();
  } catch(error) { notify(error.message); }
});
$("collect").addEventListener("click", collectSelected);
$("collect-pending").addEventListener("click", () => $("browser-mode").value === "edge" ? collectEdge(true) : collectChrome({pendingOnly:true}));
$("browser-mode").addEventListener("change", async () => {
  if (!current) return;
  await action("/api/settings", {method:"PUT",body:JSON.stringify({...current.settings,browser_mode:$("browser-mode").value})}, "浏览器选择已保存");
});
$("hours").addEventListener("change", async () => { if (current) await action("/api/settings", {method:"PUT",body:JSON.stringify({...current.settings,interval_hours:Number($("hours").value)})}); else await refresh(); });
$("refresh").addEventListener("click", refresh);
$("copy-extension-path").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText("V:\\SyncRepos\\20260923-XianyuDailyReport\\chrome-extension"); notify("扩展目录已复制"); }
  catch { notify("请在项目文件夹中选择 chrome-extension 目录"); }
});
$("seller-form").addEventListener("submit", async e => {e.preventDefault(); const result = await action("/api/sellers", {method:"POST",body:JSON.stringify({url:$("seller-url").value,name:$("seller-name").value})}, "卖家已添加"); if (result) e.target.reset();});
$("seller-list").addEventListener("change", async e => {
  if (e.target.dataset.select) await action(`/api/sellers/${encodeURIComponent(e.target.dataset.select)}`, {method:"PATCH",body:JSON.stringify({selected:e.target.checked})});
  if (e.target.dataset.name) await action(`/api/sellers/${encodeURIComponent(e.target.dataset.name)}`, {method:"PATCH",body:JSON.stringify({name:e.target.value})});
});
$("seller-list").addEventListener("click", async e => {if (e.target.dataset.remove) await action(`/api/sellers/${encodeURIComponent(e.target.dataset.remove)}`, {method:"DELETE"}, "已停止监控该卖家");});
$("favorite-list").addEventListener("change", async e => {
  if (e.target.dataset.favorite) {
    const id = e.target.dataset.favorite, selected = e.target.checked;
    const result = await action(`/api/favorites/${encodeURIComponent(id)}`, {method:"PATCH",body:JSON.stringify({selected})});
    if (result && selected) queueOwnerResolution([id]);
  }
});
$("favorite-search").addEventListener("input", () => { if (current) render(current); });
$("favorite-state-filter").addEventListener("change", () => { if (current) render(current); });
for (const [id, selected] of [["favorite-select-visible", true], ["favorite-clear-visible", false]]) {
  $(id).addEventListener("click", async () => {
    const ids = shownFavorites(current?.favorites || []).map(f => f.id);
    if (ids.length) {
      const result = await action("/api/favorites/select", {method:"POST",body:JSON.stringify({ids,selected})}, result => `已更新 ${result.updated} 件商品`);
      if (result && selected) queueOwnerResolution(ids);
    }
  });
}
$("settings-button").addEventListener("click", ()=>$("settings-dialog").showModal());
$("save-settings").addEventListener("click", async () => { const result = await action("/api/settings", {method:"PUT",body:JSON.stringify({interval_hours:Number($("hours").value),auto_collect:$("auto-collect").checked,schedule_minutes:Number($("schedule-minutes").value),browser_mode:$("browser-mode").value})}, "设置已保存"); if (result) $("settings-dialog").close();});
refresh().then(async () => {
  if (!autoAttempted && current?.settings.auto_collect && (current.sellers.some(s=>s.selected) || current.favorites?.some(f=>f.selected))) {
    autoAttempted = true; await collectSelected();
  }
});
setInterval(async () => {
  await refresh();
  if (!current?.settings.auto_collect || !(current.sellers.some(s=>s.selected) || current.favorites?.some(f=>f.selected)) || collecting) return;
  if (Date.now() - lastAutomaticAttempt < current.settings.schedule_minutes * 60000) return;
  lastAutomaticAttempt = Date.now();
  await collectSelected();
}, 60000);
