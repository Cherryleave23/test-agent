#!/usr/bin/env python3
# @module session
"""会话约束层（MOD-session P1：规划·方向B + 记忆·方向A）真实运行验收 harness。

两项改进收敛为单一产物 `UserConstraints`：
- 方向 B · 条件抽取与累积（规划）：`extract_constraints` 规则抽取 + `merge` 逐轮累积，确定性、无 LLM。
- 方向 A · 短期记忆摘要压缩（记忆）：`summarize_to_constraints` 超 N 轮触发 LLM 压缩，限制「有效信息量」而非轮数。

断言（真实运行判 PASS/FAIL，非自述）：
  B1 规则抽取：单句消息正确抽取月龄/段位/预算/过敏原/品类。
  B2 累积合并：旧值保留、新值刷新、列表去重保序、budget 以非 None 优先。
  B3 约束块注入 + 向后兼容：非空约束注入【用户已明确约束】块；None/空不注入且与旧调用完全一致。
  B4 约束持久化 round-trip：SessionStore get/save 往返一致；无约束返 None。
  A1 LLM 压缩：provider 返回 JSON → 正确解析；返回垃圾 → 兜底退化为规则抽取。
  A2 触发阈 should_compress：低于阈 False、超/等阈 True。
  A3 网关级触发：短对话(<10轮)不调 LLM 压缩；长对话(≥10轮)恰好调一次压缩。
  B5 网关级方向B：逐轮抽取累积并持久化（跨轮可续）。

直接运行：python3 test_session_constraints.py  → 退出码 0 全过，非 0 有失败。
"""
import asyncio
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from session.constraints import (  # noqa: E402
    UserConstraints,
    extract_constraints,
    summarize_to_constraints,
    should_compress,
)
from session.store import SessionStore  # noqa: E402
from agent.pipeline import Agent  # noqa: E402
from common.config import EnterpriseConfig, LLMConfig  # noqa: E402
from kb.store import KnowledgeStore  # noqa: E402
from wechat.gateway import WechatGateway  # noqa: E402
from wechat.ilink_client import IncomingMessage  # noqa: E402


# ---------------------------------------------------------------------------
# B1 规则抽取
# ---------------------------------------------------------------------------
def _b1_extract():
    c = extract_constraints(
        "宝宝6个月，喝1段奶粉，预算300，对牛奶蛋白过敏，想看奶粉"
    )
    assert c.baby_age == "6个月", f"月龄抽取错误: {c.baby_age}"
    assert c.stage == "1段", f"段位抽取错误: {c.stage}"
    assert c.budget == 300.0, f"预算抽取错误: {c.budget}"
    assert "牛奶蛋白" in c.allergens, f"过敏原抽取错误: {c.allergens}"
    assert c.category == "奶粉", f"品类抽取错误: {c.category}"

    # 空输入应返回空约束
    assert extract_constraints("").is_empty()
    assert extract_constraints("你好").is_empty()


# ---------------------------------------------------------------------------
# B2 累积合并
# ---------------------------------------------------------------------------
def _b2_merge():
    a = UserConstraints(baby_age="6个月", allergens=["牛奶蛋白"],
                        brand_preference=["A"])
    b = UserConstraints(stage="2段", allergens=["鸡蛋"],
                        brand_preference=["A", "B"], budget=300.0)
    m = a.merge(b)
    assert m.baby_age == "6个月", "旧值（B 无）应保留"
    assert m.stage == "2段", "新值应刷新"
    assert m.budget == 300.0, "budget 应以非 None 为准"
    assert m.allergens == ["牛奶蛋白", "鸡蛋"], f"列表应去重保序: {m.allergens}"
    assert m.brand_preference == ["A", "B"], f"品牌应去重保序: {m.brand_preference}"


# ---------------------------------------------------------------------------
# B3 约束块注入 + 向后兼容
# ---------------------------------------------------------------------------
def _b3_injection():
    cfg = EnterpriseConfig(enterprise_id="ent_b3",
                           system_prompt="测试系统提示")
    db = os.path.join(tempfile.mkdtemp(), "b3.db")
    store = KnowledgeStore(db, embedding_kind="mock")
    agent = Agent(cfg, store)
    ctx = "（无相关检索结果）"

    constraints = UserConstraints(
        baby_age="6个月", stage="1段", allergens=["牛奶蛋白"],
        budget=300.0, category="奶粉",
    )
    msgs = agent._build_messages("推荐奶粉", ctx, [], constraints)
    sys_content = msgs[0]["content"]
    assert "【用户已明确约束】" in sys_content, "非空约束应注入约束块"
    assert "6个月" in sys_content and "1段" in sys_content
    assert "牛奶蛋白" in sys_content and "300" in sys_content

    # 向后兼容：None 或空约束均不注入，且与老调用（无约束）输出一致
    msgs_none = agent._build_messages("推荐奶粉", ctx, [], None)
    msgs_empty = agent._build_messages("推荐奶粉", ctx, [], UserConstraints())
    assert "【用户已明确约束】" not in msgs_none[0]["content"]
    assert "【用户已明确约束】" not in msgs_empty[0]["content"]
    assert msgs_none == msgs_empty, "无约束时输出应与老调用完全一致"


# ---------------------------------------------------------------------------
# B4 约束持久化 round-trip
# ---------------------------------------------------------------------------
def _b4_persist():
    db = os.path.join(tempfile.mkdtemp(), "sess.db")
    store = SessionStore(db)
    sid = store.get_or_create("ent", "emp", "conv")
    c = UserConstraints(
        baby_age="6个月", stage="1段", allergens=["牛奶蛋白"],
        budget=300.0, brand_preference=["贝贝优"], category="奶粉",
        notes="国产优先",
    )
    store.save_constraints(sid, c)
    back = store.get_constraints(sid)
    assert back is not None
    assert back.baby_age == "6个月" and back.stage == "1段"
    assert back.budget == 300.0
    assert back.allergens == ["牛奶蛋白"]
    assert back.brand_preference == ["贝贝优"]
    assert back.category == "奶粉"
    assert back.notes == "国产优先"

    # 无约束的会话应返回 None
    sid2 = store.get_or_create("ent", "emp", "conv2")
    assert store.get_constraints(sid2) is None


# ---------------------------------------------------------------------------
# A1 LLM 压缩（JSON 解析 + 兜底退化）
# ---------------------------------------------------------------------------
async def _a1_compress():
    class JsonProvider:
        async def complete(self, messages, retrieved_hits=None, **kw):
            return json.dumps({
                "baby_age": "8个月", "stage": "2段", "allergens": ["鸡蛋"],
                "budget": None, "brand_preference": ["贝贝优"],
                "category": "奶粉", "notes": "偏好国产",
            }, ensure_ascii=False)

    c = await summarize_to_constraints("user: 宝宝8个月…", JsonProvider())
    assert c.baby_age == "8个月"
    assert c.stage == "2段"
    assert "鸡蛋" in c.allergens
    assert "贝贝优" in c.brand_preference
    assert c.category == "奶粉"

    # 兜底：provider 返回非 JSON 文本 → 退化为规则抽取
    class GarbageProvider:
        async def complete(self, messages, retrieved_hits=None, **kw):
            return "这段文本里没有 JSON，宝宝6个月 对牛奶蛋白过敏"

    c2 = await summarize_to_constraints("user: 某对话", GarbageProvider())
    assert c2.baby_age == "6个月", "兜底应从文本规则抽取到月龄"
    assert "牛奶蛋白" in c2.allergens, "兜底应抽取到过敏原"


# ---------------------------------------------------------------------------
# A2 触发阈
# ---------------------------------------------------------------------------
def _a2_threshold():
    assert should_compress(9) is False
    assert should_compress(10) is True
    assert should_compress(11) is True
    assert should_compress(0) is False


# ---------------------------------------------------------------------------
# 网关集成辅助：构造带 SpyProvider 的 gateway
# ---------------------------------------------------------------------------
class _SpyProvider:
    """记录 LLM 调用；根据 system 内容区分「压缩调用」与「回答调用」。"""

    def __init__(self):
        self.compression_calls = 0
        self.answer_calls = 0

    async def complete(self, messages, retrieved_hits=None, **kw):
        sys_content = (messages[0]["content"]
                       if messages and messages[0].get("role") == "system" else "")
        if "约束抽取器" in sys_content:  # summarize_to_constraints 的 system 标记
            self.compression_calls += 1
            return json.dumps({
                "baby_age": "8个月", "stage": "2段", "allergens": ["鸡蛋"],
                "budget": None, "brand_preference": [], "category": "奶粉",
                "notes": "",
            }, ensure_ascii=False)
        self.answer_calls += 1
        return "推荐：某奶粉（mock 回答）。"


class _FakeClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, to, content, token):
        self.sent.append((to, content))
        return {"ok": True}


def _build_gateway():
    db = os.path.join(tempfile.mkdtemp(), "gw.db")
    cfg = EnterpriseConfig(enterprise_id="ent1",
                           llm=LLMConfig(kind="mock"), db_path=db)
    store = KnowledgeStore(db, embedding_kind="mock")
    agent = Agent(cfg, store)
    spy = _SpyProvider()
    agent.provider = spy  # 注入可观测 provider
    session = SessionStore(db)
    client = _FakeClient()
    gw = WechatGateway(cfg, session, agent, client)
    return gw, spy


# ---------------------------------------------------------------------------
# A3 网关级触发：短对话不调 LLM / 长对话(≥10轮)恰好调一次压缩
# ---------------------------------------------------------------------------
async def _a3_gateway_trigger():
    # 短对话：单轮，不触发压缩（会话主键 conv 由网关取 from_user_id，无需预建）
    gw, spy = _build_gateway()
    await gw.handle_message(IncomingMessage("m1", "emp1", "你好，推荐奶粉"), None)
    assert spy.compression_calls == 0, "短对话不应触发 LLM 压缩"
    assert spy.answer_calls == 1

    # 长对话：5 轮热身(10 turns) → 第 6 轮触发压缩
    gw2, spy2 = _build_gateway()
    for i in range(5):
        await gw2.handle_message(
            IncomingMessage(f"w{i}", "emp2", "你好世界无约束"), None)
    assert spy2.compression_calls == 0, "热身轮次不应触发压缩"
    await gw2.handle_message(
        IncomingMessage("final", "emp2", "宝宝8个月喝什么"), None)
    assert spy2.compression_calls == 1, "超阈应恰好触发一次 LLM 压缩"
    assert spy2.answer_calls == 6, "每轮都应调用一次回答生成"


# ---------------------------------------------------------------------------
# B5 网关级方向B：逐轮抽取累积并持久化（跨轮可续）
# ---------------------------------------------------------------------------
async def _b5_gateway_accumulate():
    gw, _ = _build_gateway()
    # 让网关按 (ent, emp, from_user_id) 自建会话（conv = from_user_id）
    await gw.handle_message(IncomingMessage("a1", "emp3", "宝宝6个月"), None)
    sid = gw.session.get_or_create("ent1", "emp3", "emp3")  # 与网关主键一致
    stored1 = gw.session.get_constraints(sid)
    assert stored1 is not None and stored1.baby_age == "6个月"

    await gw.handle_message(
        IncomingMessage("a2", "emp3", "对牛奶蛋白过敏，预算300"), None)
    stored2 = gw.session.get_constraints(sid)
    assert stored2.baby_age == "6个月", "旧约束应保留"
    assert "牛奶蛋白" in stored2.allergens, "新约束应累积"
    assert stored2.budget == 300.0, "新约束应累积"


CHECKS = [
    ("B1 规则抽取 extract_constraints", _b1_extract, False),
    ("B2 累积合并 merge", _b2_merge, False),
    ("B3 约束块注入 + 向后兼容", _b3_injection, False),
    ("B4 约束持久化 round-trip", _b4_persist, False),
    ("A1 LLM 压缩(JSON解析 + 兜底)", _a1_compress, True),
    ("A2 触发阈 should_compress", _a2_threshold, False),
    ("A3 网关级：短不调/长调 LLM 压缩", _a3_gateway_trigger, True),
    ("B5 网关级：方向B累积并持久化", _b5_gateway_accumulate, True),
]


def main():
    failed = []
    for name, fn, is_async in CHECKS:
        try:
            if is_async:
                asyncio.run(fn())
            else:
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
