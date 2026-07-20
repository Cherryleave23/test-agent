"""Mock iLink Bot API 服务器（仅用于 harness / 本地联调，非生产）。

实现 Hermes 中确认的 iLink 契约最小集：getupdates（带 sync_buf 游标续传 +
context_token 回带）、sendmessage（按 to_user_id 记录出站消息）。
用标准库 http.server + 线程，零依赖。
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from common.config import WechatConfig


class MockILinkServer:
    def __init__(self, token: str = "test-token"):
        self.token = token
        self._lock = threading.Lock()
        self._outbox: dict[str, list[dict]] = {}      # to_user_id -> 出站消息
        self._queue: list[dict] = []                  # 待拉取入站消息
        self._cursor = 0
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---- 测试驱动接口 ----
    def inject_message(self, from_user_id: str, content: str, message_id: str) -> None:
        with self._lock:
            self._queue.append({
                "message_id": message_id,
                "from_user_id": from_user_id,
                "content": content,
                "msg_type": "text",
            })

    def sent_to(self, to_user_id: str) -> list[dict]:
        with self._lock:
            return list(self._outbox.get(to_user_id, []))

    # ---- HTTP ----
    def _handle(self, handler: BaseHTTPRequestHandler):
        length = int(handler.headers.get("Content-Length", 0) or 0)
        body = handler.rfile.read(length) if length else b""
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            payload = {}

        parsed = urlparse(handler.path)
        path = parsed.path

        if path.endswith("/getupdates"):
            with self._lock:
                since = int(payload.get("sync_buf") or 0)
                msgs = self._queue[since:]
                new_cursor = since + len(msgs)
                ctx = f"ctx-{int(time.time()*1000)}" if msgs else None
            resp = {"ret": 0, "data": {
                "sync_buf": str(new_cursor),
                "context_token": ctx,
                "messages": msgs,
            }}
        elif path.endswith("/sendmessage"):
            to = payload.get("to_user_id", "")
            with self._lock:
                self._outbox.setdefault(to, []).append({
                    "content": payload.get("content", ""),
                    "context_token": payload.get("context_token", ""),
                })
            resp = {"ret": 0, "data": {"ok": True}}
        elif path.endswith("/get_bot_qrcode"):
            resp = {"ret": 0, "data": {"qr_code": "mock-qr"}}
        elif path.endswith("/get_qrcode_status"):
            resp = {"ret": 0, "data": {"status": "scanned"}}
        else:
            resp = {"ret": -1, "data": {}}

        data = json.dumps(resp).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def start(self, host: str = "127.0.0.1", port: int = 0):
        server = HTTPServer((host, port), self._make_handler())
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return server.server_address[1]

    def shutdown(self):
        if self._server:
            self._server.shutdown()
            self._server = None

    def _make_handler(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                outer._handle(self)

            def log_message(self, *a):
                pass

        return H


def make_wechat_config(port: int, token: str = "test-token") -> WechatConfig:
    return WechatConfig(
        bot_token=token,
        app_id="bot",
        base_url=f"http://127.0.0.1:{port}",
        poll_timeout=2,
        poll_interval=0.2,
    )
