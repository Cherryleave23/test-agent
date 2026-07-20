#!/usr/bin/env python3
# @module wechat
"""真实 iLink 客户端验收（@module wechat）。

controlled-vibe-coding：真实运行判 PASS/FAIL，不自我宣称。

背景：生产闭环要求真实 ILinkClient 代码路径被验证，但 CI/沙箱不能打真实微信
（MOD-wechat §五 既定做法：起 mock iLink HTTP 服务让网关/客户端连它）。本 harness 让
【真实】ILinkClient 连本地 stub，断言：
  W1 getupdates 解析：messages / sync_buf / context_token 正确解析。
  W2 sendmessage：请求体回带 context_token 与 to_user_id（续对话连续性）。
  W3 限流：服务端返 429 → 客户端抛 RateLimitError 并退避（不洪泛、不崩溃）。

直接运行：python3 test_ilink_client.py  → 退出码 0 全过，非 0 有失败。
"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from common.config import WechatConfig  # noqa: E402
from wechat.ilink_client import ILinkClient, RateLimitError  # noqa: E402


class _State:
    def __init__(self):
        self.getupdates = []      # 收到的 getupdates 请求体
        self.sendmessages = []    # 收到的 sendmessage 请求体
        self.mode = "ok"          # ok | ratelimit


STATE = _State()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _read(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n) if n else b""
        try:
            return json.loads(body) if body else {}
        except Exception:
            return {}

    def do_POST(self):
        payload = self._read()
        p = self.path.rstrip("/")
        if p.endswith("/getupdates"):
            STATE.getupdates.append(payload)
            if STATE.mode == "ratelimit":
                self.send_response(429)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            resp = {"data": {
                "messages": [{"message_id": "m1", "from_user_id": "emp_li",
                              "content": "推荐一款1段奶粉", "msg_type": "text"}],
                "sync_buf": "buf-abc", "context_token": "ct-xyz"}}
        elif p.endswith("/sendmessage"):
            STATE.sendmessages.append(payload)
            resp = {"ret": 0, "errcode": 0}
        else:
            resp = {"ret": 0}
        data = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


async def _w1_getupdates_parse():
    STATE.getupdates.clear()
    srv, port = _start_server()
    try:
        cfg = WechatConfig(bot_token="t", app_id="bot",
                           base_url=f"http://127.0.0.1:{port}")
        client = ILinkClient(cfg)
        res = await client.get_updates(None)
        assert len(res.messages) == 1, f"应解析出 1 条消息，实际 {len(res.messages)}"
        m = res.messages[0]
        assert m.message_id == "m1" and m.from_user_id == "emp_li", f"消息字段解析错误：{m}"
        assert m.content == "推荐一款1段奶粉"
        assert res.sync_buf == "buf-abc", f"sync_buf 应解析，实际 {res.sync_buf}"
        assert res.context_token == "ct-xyz", f"context_token 应解析，实际 {res.context_token}"
    finally:
        srv.shutdown()


async def _w2_sendmessage_payload():
    STATE.sendmessages.clear()
    srv, port = _start_server()
    try:
        cfg = WechatConfig(bot_token="t", app_id="bot",
                           base_url=f"http://127.0.0.1:{port}")
        client = ILinkClient(cfg)
        await client.send_message("emp_li", "回复内容", "ct-xyz")
        assert STATE.sendmessages, "应发出 sendmessage 请求"
        body = STATE.sendmessages[-1]
        assert body.get("to_user_id") == "emp_li", f"to_user_id 应正确：{body}"
        assert body.get("content") == "回复内容"
        assert body.get("context_token") == "ct-xyz", f"必须回带 context_token 续对话：{body}"
    finally:
        srv.shutdown()


async def _w3_rate_limit():
    STATE.mode = "ratelimit"
    srv, port = _start_server()
    try:
        cfg = WechatConfig(bot_token="t", app_id="bot",
                           base_url=f"http://127.0.0.1:{port}", poll_timeout=1)
        client = ILinkClient(cfg)
        # get_updates 内部捕获 RateLimitError → 退避并返回空结果（不抛、不崩）
        res = await client.get_updates(None)
        assert res.messages == [], "429 限流下应优雅返回空，不抛异常"
    finally:
        srv.shutdown()
        STATE.mode = "ok"


CHECKS = [
    ("W1 getupdates 解析", _w1_getupdates_parse),
    ("W2 sendmessage 回带 context_token", _w2_sendmessage_payload),
    ("W3 限流 429 退避", _w3_rate_limit),
]


async def main():
    failed = []
    for name, fn in CHECKS:
        try:
            await fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {name}: 异常 {type(e).__name__}: {e}")
            failed.append(name)
    print(f"=== Summary: {len(CHECKS) - len(failed)}/{len(CHECKS)} passed ===")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("RESULT: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
