# Cloudflare Workers 部署指南

## 前置条件

1. 注册 [Cloudflare](https://dash.cloudflare.com/sign-up) 账号（免费）
2. 安装 [Wrangler CLI](https://developers.cloudflare.com/workers/wrangler/install-and-update/)：
   ```bash
   npm install -g wrangler
   ```
3. 登录：
   ```bash
   wrangler login
   ```

## 部署步骤

### 方式 A：Wrangler CLI（推荐）

```bash
cd workers
wrangler deploy ai-proxy.js --name auto-finance-ai
```

部署成功后，会显示类似：
```
✨ Successfully deployed to https://auto-finance-ai.你的用户名.workers.dev
```

### 方式 B：Cloudflare Dashboard 手动创建

1. 登录 https://dash.cloudflare.com
2. 左侧菜单 → **Workers & Pages** → **Create** → **Create Worker**
3. 粘贴 `workers/ai-proxy.js` 的代码
4. 点击 **Deploy**
5. 记住生成的 URL（如 `https://auto-finance-ai.xxx.workers.dev`）

## 前端修改

部署完成后，把前端 AI 助手的 API 地址从：
```javascript
// 旧的（本地）
const API_URL = "/api/chat";
```
改为：
```javascript
// 新的（Cloudflare Workers）
const API_URL = "https://auto-finance-ai.你的用户名.workers.dev/api/chat";
```

## 安全建议（生产环境）

当前代码把 API Key 硬编码在 Worker 里，这是**不安全**的。更好的做法：

1. 在 Cloudflare Dashboard → Workers → 你的 Worker → **Settings** → **Variables**
2. 添加 **Environment Variable**：
   - Name: `KIMI_API_KEY`
   - Value: `sk-你的实际密钥`
   - 勾选 **Encrypt**（加密存储）
3. 修改代码中 `const API_KEY = "..."` 为：
   ```javascript
   const API_KEY = env.KIMI_API_KEY;
   ```

这样 API Key 不会暴露在代码中。

## 费用

- Cloudflare Workers **免费额度**：每天 10 万次请求，足够用
- 超出后：$0.50/百万请求（几乎免费）
- Moonshot API 调用费用：按实际 tokens 计费，和本地一样

## 总结

| 组件 | 平台 | 费用 |
|------|------|------|
| 静态看板页面 | GitHub Pages | 免费 |
| AI 问答 API | Cloudflare Workers | 免费（10万次/天） |
| LLM 调用 | Moonshot API | 按量计费 |
