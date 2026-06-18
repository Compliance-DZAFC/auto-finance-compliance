// Cloudflare Workers: AI 问答助手 API 代理
// 部署后，前端调用 https://你的域名/api/chat 即可

const API_KEY = "sk-ttACQINTYwQrwKIpPIiIhDJfVkWPrYiLY14Vm1kn8SRAr5nS";  // 你的 Moonshot API Key
const API_BASE = "https://api.moonshot.cn/v1";
const MODEL = "moonshot-v1-8k";

export default {
  async fetch(request, env, ctx) {
    // 处理 CORS 预检请求
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "POST, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type",
        },
      });
    }

    // 只处理 POST 到 /api/chat
    const url = new URL(request.url);
    if (url.pathname !== "/api/chat" || request.method !== "POST") {
      return new Response(JSON.stringify({ error: "Not Found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      });
    }

    try {
      const body = await request.json();
      
      const payload = {
        model: MODEL,
        messages: body.messages || [{ role: "user", content: body.message || "" }],
        stream: true,
        temperature: 0.7,
      };

      const response = await fetch(`${API_BASE}/chat/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${API_KEY}`,
        },
        body: JSON.stringify(payload),
      });

      // 返回流式响应，并添加 CORS 头
      return new Response(response.body, {
        status: response.status,
        headers: {
          "Content-Type": "text/event-stream",
          "Access-Control-Allow-Origin": "*",
          "Cache-Control": "no-cache",
        },
      });
    } catch (e) {
      return new Response(JSON.stringify({ error: e.message }), {
        status: 500,
        headers: { 
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
        },
      });
    }
  },
};
