"""Phase B 回归：PB-3 宝宝在 mock/非结构化 LLM 下不再静默降级。

- MockProvider.supports_structured == False；结构化 provider（ollama/cloud）为 True
- 会话级宝宝能力状态可记录/读取（set 首次返回 True，之后 False）
- mock 模式下：发多条消息不触发「降级」WARNING，能力状态记为 unavailable（且每会话仅告警一次）
- 结构化 provider 但解析失败：仍走熔断降级路径（能力状态 available，降级 WARNING 出现）
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path

HERE = "/workspace"
for p in (os.path.join(HERE, "src"), os.path.join(HERE, "tools"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402
from common.config import EnterpriseConfig  # noqa: E402
from app import build_instance  # noqa: E402
from agent.providers import MockProvider, LLMProvider  # noqa: E402
from wechat.ilink_client import IncomingMessage  # noqa: E402


class _ListHandler(logging.Handler):
    def __init__(self, name):
        super().__init__(level=logging.WARNING)
        self.records = []
        logging.getLogger(name).addHandler(self)
    def emit(self, record):
        self.records.append(record.getMessage())


def _fresh_cfg():
    db = Path(tempfile.mkdtemp(prefix="pb3_")) / "instance.db"
    baby_db = Path(tempfile.mkdtemp(prefix="pb3b_")) / "baby.db"
    return EnterpriseConfig(enterprise_id="ent_sim", llm={"kind": "mock"},
                            embedding={"kind": "mock"}, rerank={"kind": "none"},
                            wechat={"bot_token": "t"},
                            db_path=str(db), baby_db_path=str(baby_db),
                            baby_profile_enabled=True)


async def _send(gateway, emp, texts):
    for i, t in enumerate(texts):
        msg = IncomingMessage(message_id=f"{emp}-{i}", from_user_id=emp, content=t)
        await gateway.handle_message(msg, None)


async def _noop_send(emp, text, ctx):
    return {"ok": True}


def test_provider_capability_flag():
    assert MockProvider().supports_structured is False
    # 结构化 provider（ollama/cloud）默认支持
    class _Struct(LLMProvider):
        async def complete(self, *a, **k):
            return "{}"
    assert _Struct().supports_structured is True


def test_session_baby_capability_roundtrip():
    from session.store import SessionStore  # noqa: E402
    db = str(Path(tempfile.mkdtemp(prefix="pb3c_")) / "s.db")
    store = SessionStore(db)
    sid = store.get_or_create("ent_sim", "emp", "emp")
    assert store.get_baby_capability(sid) == "unknown"
    assert store.set_baby_capability(sid, "unavailable") is True   # 首次
    assert store.get_baby_capability(sid) == "unavailable"
    assert store.set_baby_capability(sid, "unavailable") is False  # 重复不变
    assert store.set_baby_capability(sid, "available") is True     # 变更


def test_mock_mode_no_silent_degrade():
    cfg = _fresh_cfg()
    store, session, agent, client, gateway = build_instance(cfg)
    gateway.client.send_message = _noop_send
    handler = _ListHandler("wechat.gateway")
    sid = session.get_or_create("ent_sim", "emp_z", "emp_z")  # 整数 session_id

    async def run():
        await _send(gateway, "emp_z", ["睿护1段有什么特点", "宝宝辅食怎么加", "DHA有什么用"])
    asyncio.run(run())

    # 不应出现「降级」WARNING；能力状态应为 unavailable
    assert not any("降级" in r for r in handler.records), handler.records
    assert session.get_baby_capability(sid) == "unavailable"
    # 且应有一次「能力不可用」主动告警
    assert any("宝宝档案能力不可用" in r for r in handler.records), handler.records


def test_structured_provider_still_breaker():
    cfg = _fresh_cfg()
    store, session, agent, client, gateway = build_instance(cfg)
    handler = _ListHandler("wechat.gateway")
    sid = session.session_key("ent_sim", "emp_y", "emp_y")

    # 结构化 provider 但故意返回非 JSON → 解析失败 → 仍应走熔断降级
    class _BadStruct(LLMProvider):
        supports_structured = True
        async def complete(self, *a, **k):
            return "我不是JSON"
    agent.provider = _BadStruct()
    gateway.client.send_message = _noop_send
    sid = session.get_or_create("ent_sim", "emp_y", "emp_y")  # 整数 session_id

    async def run():
        # 连续 3 轮解析失败 → 第 4 轮起触发「降级」熔断（阈值=3，检查在每轮开头）
        await _send(gateway, "emp_y",
                    ["客户宝宝8个月", "宝宝牛奶过敏", "推荐什么奶粉", "宝宝喝几段"])
    asyncio.run(run())

    assert session.get_baby_capability(sid) == "available"
    assert any("降级" in r for r in handler.records), handler.records


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
