const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "chrome-extension", "background.js"), "utf8");
let body = "¥ 169\n343\n人想要\n2万\n浏览\n3d打印拉丝机，矿泉水瓶拉丝，可乐瓶拉丝，";
const seller = {href:"https://www.goofish.com/personal?userId=seller123", innerText:"测试卖家"};
const document = {
  title:"3d打印拉丝机_闲鱼",
  body:{innerText:body},
  querySelector(selector) {
    if (selector === "main") return {innerText:body};
    if (selector === "h1") return {innerText:"3d打印拉丝机"};
    if (selector.includes('a[href*="/personal"]')) return seller;
    return null;
  }
};
const context = vm.createContext({document, chrome:{runtime:{onMessage:{addListener(){}}}}});
vm.runInContext(source, context);
const result = vm.runInContext("itemData()", context);
assert.equal(result.views, 20000);
assert.equal(result.wants, 343);
assert.equal(result.price, "169");
body = "¥ 2180 - 2980\n191\n人想要\n2万\n浏览\nHiveton H5AM";
document.body.innerText = body;
assert.equal(vm.runInContext("itemData()", context).price, "2180 - 2980");
document.body.innerText = "糟糕！宝贝被删掉了\n为你推荐";
assert.equal(vm.runInContext("itemData()", context).deleted, true);
console.log("item detail metrics parsed correctly");
