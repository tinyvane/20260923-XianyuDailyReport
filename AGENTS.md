# 本项目协作规则

- 修改源码后，同步到用户指定的私有 GitHub 仓库 `tinyvane/20260923-XianyuDailyReport`；同步前检查分支和远端，不覆盖他人提交。
- 本地和远端源码一致；登录档案、Cookie、数据库、`.env` 不提交。当前程序只监听 `127.0.0.1:5055`，没有远程生产部署。
- 如果引入 Vite 构建，本地 `.env` 使用 localhost 开发地址，生产 `.env.production` 使用经核实的 HTTPS/WSS 域名，发布时显式执行 `npm run build -- --mode production`。
- 不把首次快照或缺失指标当成零增长；登录验证、网页结构和风控失败时要明确停止、提示。
- 用户可通过 `chrome-extension/` 连接现有 Edge/Chrome，或复用 `data/browser-profile/` 中已经扫码的固定 Edge 资料。切换浏览器必须显式选择；不能每次创建空白登录资料。登录验证既要检查会话，也要检查官方页面是否仍显示登录入口。官网由用户自行登录，不索取密码、Cookie，不尝试绕过验证码。
