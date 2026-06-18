#!/usr/bin/env python3
"""
本地 HTTP 服务器 + AI 助手 API 代理。

解决浏览器直接调用 Kimi API 的 CORS 问题：
- 前端 AI 助手请求同源的 /api/chat
- 本服务器把请求转发到 Kimi API

使用方式：
  python scripts/serve.py        # 默认端口 8000
  python scripts/serve.py 8080   # 指定端口

然后浏览器打开：http://localhost:8000/dist/index.html
"""
import os
import sys
import json
import time
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.dirname(os.path.abspath(__file__))

LLM_API_KEY = os.environ.get("KIMI_API_KEY", "sk-ttACQINTYwQrwKIpPIiIhDJfVkWPrYiLY14Vm1kn8SRAr5nS")
LLM_API_BASE = os.environ.get("KIMI_API_BASE", "https://api.moonshot.cn/v1")
LLM_MODEL = os.environ.get("KIMI_MODEL", "moonshot-v1-8k")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path == "/":
            self.path = "/dist/index.html"
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/chat":
            self.send_error(404)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")

        try:
            payload = json.loads(body)
            payload["stream"] = True
            stream_body = json.dumps(payload).encode("utf-8")

            last_error = None
            for attempt in range(3):
                req = urllib.request.Request(
                    f"{LLM_API_BASE}/chat/completions",
                    data=stream_body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {LLM_API_KEY}",
                        "Accept": "text/event-stream",
                    },
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        self.send_response(resp.status)
                        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                        self.send_header("Cache-Control", "no-cache")
                        self.send_header("Connection", "close")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()

                        try:
                            for line in resp:
                                self.wfile.write(line)
                                self.wfile.flush()
                        except (ConnectionResetError, BrokenPipeError):
                            # 客户端已关闭连接，安静退出
                            pass
                        return
                except urllib.error.HTTPError as e:
                    last_error = e
                    if e.code == 429:
                        time.sleep((attempt + 1) * 2)
                        continue
                    error_body = e.read().decode("utf-8")
                    self.send_response(e.code)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(error_body.encode("utf-8"))
                    return

            # 重试耗尽
            error_body = last_error.read().decode("utf-8") if last_error else "{}"
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(error_body.encode("utf-8"))
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"[SERVE] 启动本地服务器: http://localhost:{port}/dist/index.html")
    print(f"[SERVE] AI 代理接口: http://localhost:{port}/api/chat")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[SERVE] 已停止")


if __name__ == "__main__":
    main()
