const ORIGIN = "https://www.goofish.com";
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function sellerId(url) {
  try {
    const u = new URL(url);
    if (!["https://www.goofish.com","https://goofish.com"].includes(u.origin) || !/^\/(personal|user)/.test(u.pathname)) return null;
    const id = u.searchParams.get("userId") || u.searchParams.get("user_id") || u.searchParams.get("id");
    return /^[A-Za-z0-9_-]{4,80}$/.test(id || "") ? id : null;
  } catch { return null; }
}
function itemId(url) {
  try {
    const u = new URL(url);
    if (!["https://www.goofish.com","https://goofish.com"].includes(u.origin) || u.pathname !== "/item") return null;
    const id = u.searchParams.get("id");
    return /^\d{8,22}$/.test(id || "") ? id : null;
  } catch { return null; }
}
async function currentGoofishTab(windowId) {
  const tabs = await chrome.tabs.query({url: ["https://www.goofish.com/*", "https://goofish.com/*"]});
  const inWindow = tabs.filter(t => t.windowId === windowId);
  const preferred = inWindow.length ? inWindow : tabs;
  const tab = preferred.find(t => t.active) || preferred.sort((a,b) => (b.lastAccessed || 0) - (a.lastAccessed || 0))[0];
  if (!tab?.id) throw Error("没有找到已打开的闲鱼标签页，请在当前浏览器中打开闲鱼");
  return tab;
}
async function inject(tabId, func) {
  const result = await chrome.scripting.executeScript({target:{tabId},func});
  if (!result?.[0]) throw Error("无法读取闲鱼标签页");
  return result[0].result;
}
async function createReadClose(url, reader, ready, restoreTabId, timeoutMs = 8000) {
  const tab = await chrome.tabs.create({url, active:true});
  try {
    await chrome.tabs.update(tab.id, {autoDiscardable:false});
    const deadline = Date.now() + timeoutMs;
    let last = null;
    while (Date.now() < deadline) {
      try {
        const result = await inject(tab.id, reader);
        last = result;
        if (result?.blocked || ready(result)) return result;
      } catch (error) {
        last = {error:error.message};
      }
      await sleep(900);
    }
    if (last?.error) throw Error(`闲鱼页面读取失败：${last.error}`);
    throw Error("闲鱼页面内容加载超时");
  } finally {
    await chrome.tabs.remove(tab.id).catch(() => {});
    if (restoreTabId) await chrome.tabs.update(restoreTabId, {active:true}).catch(() => {});
  }
}
function pageSummary() {
  const text = (document.body?.innerText || "").slice(0, 2000);
  const visibleLogin = [...document.querySelectorAll("button,a,[role=button]")].some(node =>
    node.getClientRects().length && node.textContent?.trim() === "登录");
  return {
    url: location.href,
    blocked: /非法访问|安全验证|验证码|滑块/.test(text),
    loginPrompt: /请登录|扫码登录|登录后查看/.test(text),
    visibleLogin,
    personalLinks: document.querySelectorAll('a[href*="/personal"],a[href*="/user"]').length,
    hasBody: text.length > 100 && (text.includes("搜索") || location.pathname === "/collection" && text.includes("我的收藏"))
  };
}
function followingLinks() {
  const text = (document.body?.innerText || "").slice(0, 3000);
  const anchors = [...document.querySelectorAll('a[href*="/personal"],a[href*="/user"]')];
  return {
    url: location.href,
    blocked: /非法访问|安全验证|验证码|滑块|请登录|扫码登录/.test(text),
    hasFollowing: text.includes("关注"),
    candidates: anchors.map(a => ({url:a.href, name:(a.innerText || a.getAttribute("title") || "").trim().slice(0,80)}))
  };
}
async function favoriteLinks() {
  const originalScroll = window.scrollY;
  try {
    const seen = new Map();
    let stable = 0;
    for (let i=0; i<24; i++) {
      const before = seen.size;
      for (const a of document.querySelectorAll('a[href*="/item?"]')) {
        const id = new URL(a.href).searchParams.get("id");
        if (!id) continue;
        const lines = (a.innerText || "").split(/\n+/).map(x => x.trim()).filter(Boolean);
        const title = lines.find(x => x !== "取消收藏" && x !== "我想要" && !/^¥/.test(x)) || a.querySelector("img")?.alt || "";
        const price = (a.innerText || "").match(/¥\s*([\d,.]+(?:[ \t]*[-–~][ \t]*[\d,.]+)?)/)?.[1] || null;
        seen.set(id, {url:a.href,title:title.slice(0,180),price});
      }
      stable = seen.size === before ? stable + 1 : 0;
      if (seen.size >= 200 || (i >= 4 && stable >= 4)) break;
      window.scrollTo(0,document.body.scrollHeight);
      await new Promise(resolve => setTimeout(resolve,700));
    }
    const text = (document.body?.innerText || "").slice(0,2000);
    return {
      url:location.href,
      blocked:/非法访问|安全验证|验证码|滑块|请登录|扫码登录/.test(text),
      candidates:[...seen.values()].slice(0,200)
    };
  } finally { window.scrollTo(0,originalScroll); }
}
function sellerItems() {
  const text = (document.body?.innerText || "").slice(0, 2000);
  return {
    blocked: /非法访问|安全验证|验证码|滑块|请登录|扫码登录/.test(text),
    urls: [...new Set([...document.querySelectorAll('a[href*="/item?"]')].map(a => a.href))].slice(0, 80)
  };
}
function itemData() {
  const body = document.body?.innerText || "";
  const main = document.querySelector("main")?.innerText || body.slice(0, 14000);
  const text = main.slice(0, 14000);
  const first = (pattern) => {
    const hit = text.match(pattern);
    if (!hit) return null;
    const raw = hit[1];
    const scale = raw.endsWith("万") ? 10000 : raw.endsWith("千") ? 1000 : 1;
    return Math.round(parseFloat(raw) * scale);
  };
  const price = text.match(/¥\s*([\d,.]+(?:[ \t]*[-–~][ \t]*[\d,.]+)?)/)?.[1] || null;
  const views = first(/(\d+(?:\.\d+)?[万千]?)\s*(?:次)?浏览/) ?? first(/浏览[ \t]+(\d+(?:\.\d+)?[万千]?)/);
  const wants = first(/(\d+(?:\.\d+)?[万千]?)\s*人想要/) ?? first(/想要[ \t]+(\d+(?:\.\d+)?[万千]?)/);
  const title = (document.querySelector("h1")?.innerText || document.querySelector('meta[property="og:title"]')?.content || document.title.replace(/_闲鱼$/, "")).trim().slice(0,180);
  const sellerUrl = (document.querySelector('main a[href*="/personal"], main a[href*="/user"]') ||
    document.querySelector('a[href*="/personal"],a[href*="/user"]'))?.href || "";
  const sellerName = (document.querySelector('main a[href*="/personal"], main a[href*="/user"]') ||
    document.querySelector('a[href*="/personal"],a[href*="/user"]'))?.innerText?.trim().slice(0,80) || "";
  return {
    blocked: /非法访问|安全验证|验证码|滑块|请登录|扫码登录/.test(body.slice(0,1200)),
    deleted: /糟糕！宝贝被删掉了|宝贝被删掉了|商品已被删除/.test(body.slice(0,1200)),
    title, price, views, wants, sellerUrl, sellerName,
    image: document.querySelector('meta[property="og:image"]')?.content || "",
    status: /宝贝已下架|商品已下架|已售出/.test(text) ? "已下架" : "未知"
  };
}
async function handle(message, sender) {
  const {command,payload={}} = message;
  if (command === "status") {
    const tab = await currentGoofishTab(sender.tab?.windowId);
    let result;
    try {
      await chrome.tabs.update(tab.id, {active:true});
      await sleep(900);
      result = await inject(tab.id, pageSummary);
    } finally {
      if (sender.tab?.id) await chrome.tabs.update(sender.tab.id, {active:true}).catch(() => {});
    }
    const session = await chrome.cookies.get({url:ORIGIN,name:"unb"});
    if (result.blocked || result.loginPrompt || result.visibleLogin || !result.hasBody || !session)
      throw Error("闲鱼页面正在验证、未登录或尚未加载完成");
    return {ok:true, url:result.url, personalLinks:result.personalLinks};
  }
  if (command === "following") {
    const tab = await currentGoofishTab(sender.tab?.windowId);
    const result = await inject(tab.id, followingLinks);
    if (result.blocked || !result.hasFollowing || !/\/(personal|user|follow)/i.test(new URL(result.url).pathname))
      throw Error("请先在已登录的闲鱼标签页打开自己的关注列表");
    const candidates = result.candidates.filter(c => sellerId(c.url)).slice(0,300);
    return {candidates};
  }
  if (command === "favorites") {
    const tabs = await chrome.tabs.query({url:["https://www.goofish.com/collection*", "https://goofish.com/collection*"]});
    const tab = tabs.find(t => t.windowId === sender.tab?.windowId) || tabs[0];
    if (!tab?.id) throw Error("请先在当前浏览器打开闲鱼「我的收藏」页面");
    let result;
    try {
      await chrome.tabs.update(tab.id, {active:true});
      await sleep(900);
      result = await inject(tab.id, favoriteLinks);
    } finally {
      if (sender.tab?.id) await chrome.tabs.update(sender.tab.id, {active:true}).catch(() => {});
    }
    if (result.blocked || new URL(result.url).pathname !== "/collection")
      throw Error("请先在已登录的闲鱼标签页打开「我的收藏」有效宝贝页");
    const candidates = result.candidates.filter(c => itemId(c.url)).slice(0,200);
    if (!candidates.length) throw Error("收藏页没有找到可识别的商品链接，请确认处于「有效宝贝」且商品卡片已加载");
    return {candidates};
  }
  if (command === "listItems") {
    if (!sellerId(payload.url)) throw Error("卖家主页链接无效");
    const result = await createReadClose(payload.url, sellerItems, value => value.urls?.length > 0, sender.tab?.id, 15000);
    if (result.blocked) throw Error("卖家页面要求登录或安全验证");
    return {urls:result.urls.filter(u => itemId(u)).slice(0,40)};
  }
  if (command === "readItem") {
    if (!itemId(payload.url)) throw Error("商品链接无效");
    const openTabs = await chrome.tabs.query({url:["https://www.goofish.com/item*", "https://goofish.com/item*"]});
    const existing = openTabs.find(t => itemId(t.url) === itemId(payload.url));
    let result;
    if (existing) {
      try {
        await chrome.tabs.update(existing.id, {active:true});
        await sleep(900);
        result = await inject(existing.id, itemData);
      } finally {
        if (sender.tab?.id) await chrome.tabs.update(sender.tab.id, {active:true}).catch(() => {});
      }
    } else {
      result = await createReadClose(payload.url, itemData,
        value => value.deleted || value.title && value.sellerUrl && (value.price != null || value.views != null || value.wants != null), sender.tab?.id);
    }
    if (result.blocked) throw Error("商品页面要求登录或安全验证");
    if (result.deleted) throw Error("商品已被删除");
    if (!result.title || (result.price == null && result.views == null && result.wants == null))
      throw Error("商品页面没有可识别的标题和指标");
    if (!sellerId(result.sellerUrl) || (payload.sellerId && sellerId(result.sellerUrl) !== payload.sellerId))
      throw Error("商品页未能确认属于所选卖家");
    return {item:{...result,sellerId:sellerId(result.sellerUrl)}};
  }
  throw Error("未知指令");
}
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!/^http:\/\/(127\.0\.0\.1|localhost):5055\//.test(sender.url || sender.tab?.url || "")) return;
  handle(message, sender).then(sendResponse).catch(error => sendResponse({error:error.message || "读取失败"}));
  return true;
});
