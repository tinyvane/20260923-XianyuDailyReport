const $ = id => document.getElementById(id);
let current = null;
let toastTimer;
let collecting = false;
let autoAttempted = false;
let lastAutomaticAttempt = 0;
const esc = text => String(text ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const number = n => n == null ? "—" : (n > 0 ? "+" : "") + n.toLocaleString("zh-CN");
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
  $("report-date").textContent = data.date;
  $("hours").value = String(data.hours);
  $("interval-label").textContent = data.hours;
  $("valid-count").textContent = data.run ? `${data.valid}/${data.found}` : "—";
  const state = data.run?.state || "等待首次采集";
  $("data-state").textContent = state;
  $("data-state").classList.toggle("warn", state !== "完成");
  $("latest-run").textContent = data.run ? `最近采集：${new Date(data.run.finished_at || data.run.started_at).toLocaleString("zh-CN")} · ${data.run.message}` : "尚无采集记录";
  $("views-total").textContent = number(data.views_delta);
  $("wants-total").textContent = number(data.wants_delta);
  $("count-unknown").textContent = data.count_anomalies;
  $("price-change").textContent = data.price_changes == null ? "—" : data.price_changes;
  $("missing-count").textContent = data.uncomparable;
  if (!collecting) $("control-status").textContent = `闲鱼登录：${data.login} · 已选 ${data.sellers.filter(s=>s.selected).length} 位卖家`;
  $("collect").disabled = collecting;
  $("ranking-note").textContent = data.top.length ? `按最近 ${data.hours} 小时浏览增长排序` : "";
  $("ranking").innerHTML = data.top.length ? data.top.map((item, i) => {
    const seller = data.sellers.find(s => s.id === item.seller_id);
    return `<div class="rank-row"><span class="rank">${i+1}</span><span class="rank-gray">${i+1}</span>${item.image ? `<img src="${esc(item.image)}" alt="" referrerpolicy="no-referrer">` : '<span class="avatar-placeholder">▣</span>'}<a class="name" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer" title="${esc(item.title)}">${esc(item.title)}</a><span class="seller-name">${esc(seller?.name || "未知卖家")}</span><span class="deltas"><b class="view">${number(item.views_delta)}浏览</b>，<b class="want">${number(item.wants_delta)}想要</b></span></div>`;
  }).join("") : '<div class="empty-state">还没有可对比的数据。完成首次采集后，经过选定时长再采集即可看到增长榜。</div>';
  $("seller-note").textContent = data.sellers.length ? `${data.sellers.filter(s=>s.selected).length} 位重点卖家` : "";
  $("seller-report").innerHTML = data.sellers.filter(s=>s.selected).length ? data.sellers.filter(s=>s.selected).map(s => `<div class="seller-bullet"><span><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.name)}</a>：监控${s.count}件商品，合计${number(s.views_delta)}浏览、${number(s.wants_delta)}想要</span></div>`).join("") : '<div class="empty-state">尚未选择重点卖家。请在已登录的 Chrome 关注列表导入并勾选，或在下方手动添加主页链接。</div>';
  $("seller-list").innerHTML = data.sellers.length ? data.sellers.map(s => `<div class="seller-chip"><input type="checkbox" data-select="${esc(s.id)}" aria-label="监控 ${esc(s.name)}" ${s.selected ? "checked" : ""}><input class="chip-name" data-name="${esc(s.id)}" aria-label="卖家备注" value="${esc(s.name)}"><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">主页 ↗</a><button type="button" data-remove="${esc(s.id)}" aria-label="取消监控 ${esc(s.name)}">×</button></div>`).join("") : '<p class="list-empty">暂无卖家</p>';
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
      reject(Error("未收到 Chrome 扩展响应。请在 chrome://extensions 加载本项目 chrome-extension 文件夹，并刷新日报页面"));
    }, command === "status" ? 7000 : command === "following" ? 12000 : 55000);
    function receive(event) {
      if (event.source !== window || event.origin !== location.origin || event.data?.source !== "xianyu-report-extension" || event.data.id !== id) return;
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
async function collectChrome() {
  if (collecting) return;
  collecting = true;
  $("collect").disabled = true;
  $("control-status").textContent = "正在连接已登录的 Chrome";
  let run = null, found = 0, valid = 0;
  const errors = [];
  try {
    await chromeStatus();
    const sellers = current.sellers.filter(s => s.selected);
    if (!sellers.length) throw Error("请先在监控名单中勾选重点卖家");
    run = await api("/api/chrome/run/start", {method:"POST"});
    for (const seller of sellers) {
      try {
        $("control-status").textContent = `正在读取 ${seller.name} 的商品`;
        const list = await extensionCall("listItems", {url:seller.url});
        if (!list.urls.length) { errors.push(`${seller.name}：未发现可识别商品`); continue; }
        for (const url of list.urls) {
          found++;
          try {
            $("control-status").textContent = `正在采集 ${seller.name} · ${valid}/${found} 件有效`;
            const result = await extensionCall("readItem", {url,sellerId:seller.id});
            await api("/api/chrome/run/item", {method:"POST",body:JSON.stringify({...result.item,seller_id:seller.id,url})});
            valid++;
          } catch(error) { errors.push(`${seller.name} / 商品：${error.message}`); }
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
$("open-browser").addEventListener("click", async () => {
  try { await chromeStatus(); notify("已连接当前 Chrome 闲鱼标签页"); }
  catch(error) { notify(error.message); }
});
$("check-login").addEventListener("click", async () => {
  try { await chromeStatus(); notify("Chrome 闲鱼会话可用"); }
  catch(error) { await api("/api/chrome/status", {method:"POST",body:JSON.stringify({ok:false})}).catch(()=>{}); notify(error.message); await refresh(); }
});
$("import-following").addEventListener("click", async () => {
  try {
    await chromeStatus();
    const result = await extensionCall("following");
    const saved = await api("/api/chrome/following", {method:"POST",body:JSON.stringify(result.candidates)});
    notify(`新增 ${saved.imported} 位候选卖家，请在监控名单中勾选重点对象`);
    await refresh();
  } catch(error) { notify(error.message); }
});
$("collect").addEventListener("click", collectChrome);
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
$("settings-button").addEventListener("click", ()=>$("settings-dialog").showModal());
$("save-settings").addEventListener("click", async () => { const result = await action("/api/settings", {method:"PUT",body:JSON.stringify({interval_hours:Number($("hours").value),auto_collect:$("auto-collect").checked,schedule_minutes:Number($("schedule-minutes").value)})}, "设置已保存"); if (result) $("settings-dialog").close();});
refresh().then(async () => {
  if (!autoAttempted && current?.settings.auto_collect && current.sellers.some(s=>s.selected)) {
    autoAttempted = true; lastAutomaticAttempt = Date.now(); await collectChrome();
  }
});
setInterval(async () => {
  await refresh();
  if (!current?.settings.auto_collect || !current.sellers.some(s=>s.selected) || collecting) return;
  if (Date.now() - lastAutomaticAttempt < current.settings.schedule_minutes * 60000) return;
  lastAutomaticAttempt = Date.now();
  await collectChrome();
}, 60000);
