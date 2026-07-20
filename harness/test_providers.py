#!/usr/bin/env python3
# @module agent
"""真实 LLM provider 验收（@module agent）。

controlled-vibe-coding：真实运行判 PASS/FAIL，不自我宣称。

背景：生产闭环要求真实 OllamaProvider / CloudProvider 代码路径被验证，但沙箱无 ollama 二进制、
也无云 API 凭证。做法（与 MOD-wechat §五 一致）：起**本地 stub HTTP 服务**模拟 ollama / OpenAI
端点，让【真实】provider 发请求并断言：
  P1 ollama：请求体含 model / messages / temperature 且 stream:false（修复流式 NDJSON 崩溃）；
            正确解析 message.content；RAG 上下文（【企业知识库】）确实进入其收到的 messages。
  P2 cloud：请求带 Bearer 鉴权头；请求体含 model / messages / temperature / max_tokens；
            正确解析 choices[0].message.content。

直接运行：python3 test_providers.py  → 退出码 0 全过，非 0 有失败。
"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from common.config import LLMConfig, EnterpriseConfig  # noqa: E402
from agent.providers import OllamaProvider, CloudProvider  # noqa: E402
from agent.pipeline import Agent  # noqa: E402  (仅用其 _build_messages 验证 grounding 透传)


class _Rec:
    def __init__(self):
        self.requests = []


REC = _Rec()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默
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
        REC.requests.append({
            "path": self.path,
            "auth": self.headers.get("Authorization", ""),
            "payload": payload,
        })
        p = self.path.rstrip("/")
        if p.endswith("/api/chat"):
            resp = {"message": {"role": "assistant", "content": "Ollama 回复"}, "done": True}
        elif p.endswith("/chat/completions"):
            resp = {"choices": [{"message": {"role": "assistant", "content": "Cloud 回复"}}]}
        else:
            resp = {"ok": True}
        data = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


async def _p1_ollama_real_path():
    REC.requests.clear()
    srv, port = _start_server()
    try:
        cfg = LLMConfig(kind="ollama", model="qwen2.5:7b",
                        base_url=f"http://127.0.0.1:{port}")
        prov = OllamaProvider(cfg)
        out = await prov.complete([{"role": "user", "content": "你好"}])
        assert out == "Ollama 回复", f"ollama 应解析 message.content，实际 {out!r}"
        reqs = [r for r in REC.requests if r["path"].rstrip("/").endswith("/api/chat")]
        assert reqs, "ollama 应请求 /api/chat"
        body = reqs[-1]["payload"]
        assert body.get("model") == "qwen2.5:7b", f"model 缺失/不符：{body}"
        assert body.get("stream") is False, f"ollama 须 stream:false（否则真实服务返回 NDJSON 崩溃）：{body}"
        assert isinstance(body.get("messages"), list) and body["messages"], "messages 应为非空列表"
        assert body["options"]["temperature"] == 0.2, "temperature 应透传"
    finally:
        srv.shutdown()


async def _p2_cloud_real_path():
    REC.requests.clear()
    srv, port = _start_server()
    try:
        cfg = LLMConfig(kind="cloud", model="gpt-4o-mini", api_key="sk-test",
                        base_url=f"http://127.0.0.1:{port}", max_tokens=512)
        prov = CloudProvider(cfg)
        out = await prov.complete([{"role": "user", "content": "你好"}])
        assert out == "Cloud 回复", f"cloud 应解析 choices[0].message.content，实际 {out!r}"
        reqs = [r for r in REC.requests if r["path"].rstrip("/").endswith("/chat/completions")]
        assert reqs, "cloud 应请求 /chat/completions"
        body = reqs[-1]["payload"]
        assert body.get("model") == "gpt-4o-mini", f"model 缺失：{body}"
        assert body.get("max_tokens") == 512, "max_tokens 应透传"
        assert body["temperature"] == 0.2, "temperature 应透传"
        assert reqs[-1]["auth"].startswith("Bearer sk-test"), f"cloud 须带 Bearer 鉴权头：{reqs[-1]['auth']}"
    finally:
        srv.shutdown()


async def _p3_grounding_reaches_provider():
    # RAG grounding 在 pipeline 层把【企业知识库】注入 system prompt；
    # provider 只转发 messages。验证"pipeline 发出什么，真实 provider 就收到什么"（无截断）。
    REC.requests.clear()
    srv, port = _start_server()
    try:
        cfg = LLMConfig(kind="ollama", model="m", base_url=f"http://127.0.0.1:{port}")
        # Agent 需要 EnterpriseConfig（内部读 cfg.llm）；此处只用到 _build_messages，store 可为 None
        agent = Agent(EnterpriseConfig(enterprise_id="ent_x", llm=cfg), None)
        ctx = "【引用1】睿护婴儿配方奶粉1段\n生牛乳、脱盐乳清粉"
        msgs = agent._build_messages("推荐一款1段奶粉", ctx, history=[])
        sys_content = msgs[0]["content"]
        assert "【企业知识库】" in sys_content and "睿护" in sys_content, \
            f"grounding 上下文应进入 system prompt：{sys_content[:80]}"
        prov = OllamaProvider(cfg)
        await prov.complete(msgs)
        reqs = [r for r in REC.requests if r["path"].rstrip("/").endswith("/api/chat")]
        sent = reqs[-1]["payload"]["messages"]
        assert any("【企业知识库】" in m["content"] and "睿护" in m["content"] for m in sent), \
            "provider 收到的 messages 应含完整 RAG 上下文"
    finally:
        srv.shutdown()


CHECKS = [
    ("P1 ollama 真实路径", _p1_ollama_real_path),
    ("P2 cloud 真实路径", _p2_cloud_real_path),
    ("P3 grounding 透传", _p3_grounding_reaches_provider),
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
