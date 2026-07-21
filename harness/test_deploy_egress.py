#!/usr/bin/env python3
# @module deploy
"""出入站白名单（MOD-deploy P0-2）真实运行验收 harness。

按 CVC：真实策略判定 + 真实客户端拦截行为，断言 PASS/FAIL，非自述。
覆盖：
  E1 默认白名单域名放行（ilinkai / CDN）
  E2 强制关闭时不拦截（开发/mock 透传）
  E3 强制开启时非白名单域名抛 EgressBlocked
  E4 显式配置的 LLM base_url 主机自动并入白名单
  E5 AGENT_EGRESS_EXTRA_HOSTS 逃生阀放通
  E6 AllowedAsyncClient 拦截在发请求之前（坏域名，无真实出网）
  E7 AllowedAsyncClient 对白名单域名正常转发请求

直接运行：python3 test_deploy_egress.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
import asyncio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common.egress import (  # noqa: E402
    EgressPolicy, EgressBlocked, get_policy, reset_policy, configure_egress,
    AllowedAsyncClient,
)


def _policy(enforce, **kw):
    reset_policy()
    return configure_egress(enforce=enforce, **kw)


def e1_default_allowlist_pass():
    p = _policy(enforce=True)
    # 默认白名单域名应放行，不抛错
    p.assert_allowed("https://ilinkai.weixin.qq.com/getupdates")
    p.assert_allowed("https://novac2c.cdn.weixin.qq.com/media/abc")
    p.assert_allowed("ilinkai.weixin.qq.com")  # 裸主机名也认


def e2_enforce_off_passthrough():
    p = _policy(enforce=False)
    # 强制关闭：任意域名透传，不拦截
    p.assert_allowed("https://evil.example.com/secret")
    p.assert_allowed("http://10.0.0.5/internal")


def e3_enforce_on_blocks():
    p = _policy(enforce=True)
    for bad in ("https://evil.example.com/x", "http://10.0.0.5/y", "https://api.evil.test/z"):
        raised = False
        try:
            p.assert_allowed(bad)
        except EgressBlocked:
            raised = True
        assert raised, f"强制模式下应拦截非白名单域名：{bad}"


def e4_base_url_auto_allowed():
    p = _policy(enforce=True, llm_base_url="https://api.openai.com/v1")
    # 配置的 LLM 端点主机自动并入白名单
    p.assert_allowed("https://api.openai.com/v1/chat/completions")
    # 仍拦截其它域名
    raised = False
    try:
        p.assert_allowed("https://other-llm.example.com/x")
    except EgressBlocked:
        raised = True
    assert raised, "其它 LLM 域名仍应被拦截"


def e5_extra_hosts_escape_hatch():
    p = _policy(enforce=True, extra_hosts=["mirror.corp.example.com"])
    p.assert_allowed("https://mirror.corp.example.com/pkg")
    raised = False
    try:
        p.assert_allowed("https://not-in-extra.example.com/x")
    except EgressBlocked:
        raised = True
    assert raised, "非 extra 域名仍应被拦截"


# ---- AllowedAsyncClient 行为（假 httpx，无真实出网）-------------------------
class _FakeResp:
    def __init__(self, text="ok"):
        self.text = text

    def json(self):
        return {"ok": True}


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *x):
        return None

    async def post(self, url, **k):
        self.calls.append(url)
        return _FakeResp()


def _with_fake_httpx(fn):
    import httpx

    orig = httpx.AsyncClient
    httpx.AsyncClient = _FakeClient
    try:
        return fn()
    finally:
        httpx.AsyncClient = orig


def e6_async_client_blocks_before_network():
    p = _policy(enforce=True)  # 不并入 evil 域名

    def run():
        fake = _FakeClient()
        import httpx

        orig = httpx.AsyncClient
        httpx.AsyncClient = lambda *a, **k: fake
        try:
            async def go():
                try:
                    async with AllowedAsyncClient(timeout=5) as c:
                        await c.post("https://evil.example.com/steal")
                    return ("no_raise", fake.calls)
                except EgressBlocked:
                    return ("raised", fake.calls)
            return asyncio.run(go())
        finally:
            httpx.AsyncClient = orig

    status, calls = run()
    assert status == "raised", "坏域名应抛 EgressBlocked"
    assert calls == [], "拦截必须发生在发请求之前（无真实出网）"


def e7_async_client_forwards_allowed():
    p = _policy(enforce=True)
    p.allow("http://allowed.test")

    def run():
        import httpx

        orig = httpx.AsyncClient
        fake = _FakeClient()
        httpx.AsyncClient = lambda *a, **k: fake
        try:
            async def go():
                async with AllowedAsyncClient(timeout=5) as c:
                    r = await c.post("http://allowed.test/path", json={"a": 1})
                return fake.calls, r
            return asyncio.run(go())
        finally:
            httpx.AsyncClient = orig

    calls, resp = run()
    assert calls == ["http://allowed.test/path"], f"白名单域名应转发：{calls}"
    assert resp.json() == {"ok": True}


CHECKS = [
    ("E1 默认白名单域名放行", e1_default_allowlist_pass),
    ("E2 强制关闭透传(开发/mock)", e2_enforce_off_passthrough),
    ("E3 强制开启拦截非白名单", e3_enforce_on_blocks),
    ("E4 LLM base_url 自动并入白名单", e4_base_url_auto_allowed),
    ("E5 EXTRA_HOSTS 逃生阀", e5_extra_hosts_escape_hatch),
    ("E6 客户端拦截在出网之前", e6_async_client_blocks_before_network),
    ("E7 客户端转发白名单域名", e7_async_client_forwards_allowed),
]


def main():
    failed = []
    for name, fn in CHECKS:
        try:
            fn()
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
    sys.exit(main())
