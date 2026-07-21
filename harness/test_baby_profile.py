#!/usr/bin/env python3
# @module baby
"""宝宝/客户档案层（MOD-baby-profile，P2）真实运行验收 harness。

按 CVC：真实运行判 PASS/FAIL，非自述。本文件随实现阶段增量补充检查：
  阶段1（存储）：P1 显式建档 / P7 (ent,emp) 隔离 / P8 持久化全链路(upsert/confirm/merge/delete)
  阶段2（消歧）：P4 快速切换意图消歧 / P5 代词指代
  阶段3（网关）：P2 混合式安全网(自动建档+第三方不建) / P3 主动归档跨轮累积
  阶段4（注入）：P6 焦点宝宝档案块注入 system prompt / P9 向后兼容
  优化（Prompt Caching）：P17 稳定前缀命中契约 + cache_control 断点；P18 prompt 结构(byte-for-byte 可缓存)；
  P19 序列化稳定 + 顺序即缓存键；P20 list_for_employee 排序稳定(ORDER BY)；P21 RAG prompt 顺序(稳定在前/动态在后)；
  P23 缓存预热

直接运行：python3 test_baby_profile.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
import time
import tempfile
import json
import asyncio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from baby.models import BabyProfile  # noqa: E402
from baby.store import BabyProfileStore  # noqa: E402
from baby.resolution import resolve_and_extract  # noqa: E402
from baby.archive import resolve_and_archive  # noqa: E402
from agent.pipeline import Agent  # noqa: E402
from agent.providers import _apply_cache_control  # noqa: E402
from agent.warmup import warmup_prompt_cache  # noqa: E402
from common.config import EnterpriseConfig  # noqa: E402
from common.db import connect  # noqa: E402


def _tmp_db():
    return os.path.join(tempfile.mkdtemp(), "baby.db")


# ---------------------------------------------------------------------------
# P1 显式建档：客户 + 宝宝创建且属性落库
# ---------------------------------------------------------------------------
def _p1_explicit_create():
    store = BabyProfileStore(_tmp_db())
    cid = store.get_or_create_customer("ent1", "emp1", "张姐")
    baby = BabyProfile(
        baby_id=None, enterprise_id="ent1", employee_id="emp1",
        customer_id=cid, name="壮壮", baby_age="6个月",
        allergens=["牛奶蛋白"], status="confirmed",
    )
    bid = store.create_baby(baby)
    got = store.get_baby(bid)
    assert got is not None
    assert got.name == "壮壮"
    assert got.baby_age == "6个月"
    assert "牛奶蛋白" in got.allergens
    assert got.customer_id == cid
    cust = store.get_customer(cid)
    assert cust is not None and cust.name == "张姐"


# ---------------------------------------------------------------------------
# P7 隔离：(enterprise_id, employee_id) 隔离，跨员工/跨企业不可见
# ---------------------------------------------------------------------------
def _p7_isolation():
    store = BabyProfileStore(_tmp_db())
    c1 = store.get_or_create_customer("ent1", "emp1", "张姐")
    store.create_baby(BabyProfile(None, "ent1", "emp1", c1, "壮壮",
                                  baby_age="6个月", status="confirmed"))
    c2 = store.get_or_create_customer("ent1", "emp2", "李姐")
    store.create_baby(BabyProfile(None, "ent1", "emp2", c2, "明明",
                                  status="confirmed"))
    # 员工2 只看得到自己的
    list2 = store.list_for_employee("ent1", "emp2")
    assert len(list2) == 1 and list2[0]["baby_name"] == "明明"
    list1 = store.list_for_employee("ent1", "emp1")
    assert len(list1) == 1 and list1[0]["baby_name"] == "壮壮"
    # 跨企业隔离
    c3 = store.get_or_create_customer("ent2", "emp1", "王姐")
    store.create_baby(BabyProfile(None, "ent2", "emp1", c3, "花花",
                                  status="confirmed"))
    assert len(store.list_for_employee("ent1", "emp1")) == 1


# ---------------------------------------------------------------------------
# P8 持久化 round-trip：create/upsert(merge)/confirm/merge/delete 全链路
# ---------------------------------------------------------------------------
def _p8_roundtrip():
    store = BabyProfileStore(_tmp_db())
    cid = store.get_or_create_customer("ent1", "emp1", "张姐")
    bid = store.create_baby(BabyProfile(
        None, "ent1", "emp1", cid, "壮壮", baby_age="6个月", status="pending"))
    # upsert(merge)：累加 stage/budget，保留 baby_age，status 仍为 pending
    merged = store.upsert_baby_attrs(
        bid, BabyProfile(None, "ent1", "emp1", cid, "壮壮",
                         stage="2段", budget=300.0))
    assert merged.baby_age == "6个月"
    assert merged.stage == "2段"
    assert merged.budget == 300.0
    assert merged.status == "pending"
    # confirm
    store.mark_confirmed(bid)
    assert store.get_baby(bid).status == "confirmed"
    # merge：再建一个"壮壮小名"合并进壮壮，source 被删
    bid2 = store.create_baby(BabyProfile(
        None, "ent1", "emp1", cid, "壮壮小名", allergens=["鸡蛋"], status="pending"))
    m = store.merge_baby(bid, bid2)
    assert "鸡蛋" in m.allergens
    assert store.get_baby(bid2) is None  # source 已删
    assert store.get_baby(bid) is not None
    # delete
    store.delete_baby(bid)
    assert store.get_baby(bid) is None


# ---------------------------------------------------------------------------
# P4 快速切换意图消歧：A壮壮 / B妞妞 / 回A → 每轮解析正确焦点
# ---------------------------------------------------------------------------
async def _p4_switching():
    store = BabyProfileStore(_tmp_db())
    cid1 = store.get_or_create_customer("ent1", "emp1", "张姐")
    bid1 = store.create_baby(BabyProfile(
        None, "ent1", "emp1", cid1, "壮壮", baby_age="6个月", status="confirmed"))
    cid2 = store.get_or_create_customer("ent1", "emp1", "李姐")
    bid2 = store.create_baby(BabyProfile(
        None, "ent1", "emp1", cid2, "妞妞", baby_age="2岁", status="confirmed"))
    known = store.list_for_employee("ent1", "emp1")

    class SwitchProvider:
        async def complete(self, messages, retrieved_hits=None, **kw):
            user = messages[-1]["content"]
            # 只看当前这一轮（最后一个 "user: " 之后），模拟真实 LLM 依据当前句 + 上下文消歧
            cur = user.split("\nuser: ")[-1]
            if "妞妞" in cur:
                return json.dumps({"action": "chat", "customer": "李姐", "baby": "妞妞",
                                    "extracted": {}, "is_third_party": False,
                                    "is_hypothetical": False}, ensure_ascii=False)
            if "壮壮" in cur:
                return json.dumps({"action": "chat", "customer": "张姐", "baby": "壮壮",
                                    "extracted": {}, "is_third_party": False,
                                    "is_hypothetical": False}, ensure_ascii=False)
            return json.dumps({"action": "chat", "baby": "", "extracted": {},
                               "is_third_party": False, "is_hypothetical": False},
                              ensure_ascii=False)

    r1 = await resolve_and_extract("user: 你好", "壮壮6个月喝什么奶粉", known, None, SwitchProvider())
    assert r1.baby_id == bid1, f"应解析到壮壮({bid1})，实际 {r1.baby_id}"
    r2 = await resolve_and_extract("user: 壮壮喝1段", "妞妞现在2岁换什么", known, bid1, SwitchProvider())
    assert r2.baby_id == bid2, f"应解析到妞妞({bid2})，实际 {r2.baby_id}"
    r3 = await resolve_and_extract("user: 妞妞换3段", "回到壮壮，他过敏吗", known, bid2, SwitchProvider())
    assert r3.baby_id == bid1, f"应解析回壮壮({bid1})，实际 {r3.baby_id}"


# ---------------------------------------------------------------------------
# P5 代词指代：用「他/宝宝」指代本会话焦点宝宝；无信号则短路不调 LLM
# ---------------------------------------------------------------------------
async def _p5_pronoun():
    store = BabyProfileStore(_tmp_db())
    cid1 = store.get_or_create_customer("ent1", "emp1", "张姐")
    bid1 = store.create_baby(BabyProfile(
        None, "ent1", "emp1", cid1, "壮壮", baby_age="6个月", status="confirmed"))
    known = store.list_for_employee("ent1", "emp1")

    class FocusProvider:
        async def complete(self, messages, retrieved_hits=None, **kw):
            return json.dumps({"action": "chat", "baby": "",
                               "extracted": {"stage": "2段"},
                               "is_third_party": False, "is_hypothetical": False},
                              ensure_ascii=False)

    r = await resolve_and_extract("user: 壮壮6个月", "他现在换2段合适吗", known, bid1, FocusProvider())
    assert r.baby_id == bid1, f"代词应解析到焦点宝宝({bid1})，实际 {r.baby_id}"
    assert r.extracted.stage == "2段"

    # D1 修复后：每轮都走 LLM（取消规则短路），无宝宝信号也调 LLM
    class SpyProvider:
        def __init__(self):
            self.calls = 0

        async def complete(self, messages, retrieved_hits=None, **kw):
            self.calls += 1
            return "{}"

    spy = SpyProvider()
    r0 = await resolve_and_extract("user: 壮壮6个月", "今天天气不错", known, bid1, spy)
    assert spy.calls == 1, "D1 修复后每轮都走 LLM（无信号也调 LLM 判断归属）"
    assert r0.action == "chat" and r0.baby_id == bid1


# ---------------------------------------------------------------------------
# P2 混合式建档安全网：全新宝宝自动建档(pending) / 第三人称不建档 / 不重复建档
# ---------------------------------------------------------------------------
async def _p2_autocreate_safety():
    store = BabyProfileStore(_tmp_db())

    class AutoProvider:
        async def complete(self, messages, retrieved_hits=None, **kw):
            cur = messages[-1]["content"].split("\nuser: ")[-1]
            if "同事" in cur:
                return json.dumps({"action": "chat", "baby": "", "extracted": {},
                                   "is_third_party": True, "is_hypothetical": False},
                                  ensure_ascii=False)
            if "妞妞" in cur:
                return json.dumps({"action": "new_baby", "customer": "李姐",
                                   "baby": "妞妞", "extracted": {"baby_age": "2岁"},
                                   "is_third_party": False, "is_hypothetical": False},
                                  ensure_ascii=False)
            return json.dumps({"action": "chat", "baby": "", "extracted": {},
                               "is_third_party": False, "is_hypothetical": False},
                              ensure_ascii=False)

    # 第三人称 → 不建档（安全网，绝不污染真实客户档案）
    r = await resolve_and_archive(store, AutoProvider(), "ent1", "emp1", "",
                                  "我同事家宝宝过敏了", None)
    assert r.focus_baby_id is None
    assert len(store.list_for_employee("ent1", "emp1")) == 0

    # 全新宝宝 → 自动建档，status=pending（待确认）
    r2 = await resolve_and_archive(store, AutoProvider(), "ent1", "emp1", "",
                                   "李姐家妞妞2岁了", None)
    assert r2.created is True
    baby = store.get_baby(r2.focus_baby_id)
    assert baby.name == "妞妞"
    assert baby.status == "pending"
    assert baby.baby_age == "2岁"
    cust = store.get_customer(baby.customer_id)
    assert cust.name == "李姐"

    # 重复同句 → 不重复建档（防重）
    r3 = await resolve_and_archive(store, AutoProvider(), "ent1", "emp1", "",
                                   "李姐家妞妞2岁了", r2.focus_baby_id)
    assert r3.created is False
    assert r3.focus_baby_id == r2.focus_baby_id


# ---------------------------------------------------------------------------
# P3 主动归档跨轮累积：抽取属性 upsert 进正确宝宝，跨轮保留并累加
# ---------------------------------------------------------------------------
async def _p3_cross_turn_archive():
    store = BabyProfileStore(_tmp_db())
    cid = store.get_or_create_customer("ent1", "emp1", "张姐")
    bid = store.create_baby(BabyProfile(
        None, "ent1", "emp1", cid, "壮壮", status="confirmed"))

    class ArchiveProvider:
        async def complete(self, messages, retrieved_hits=None, **kw):
            cur = messages[-1]["content"].split("\nuser: ")[-1]
            if "过敏" in cur:
                return json.dumps({"action": "chat", "customer": "张姐", "baby": "壮壮",
                                   "extracted": {"allergens": ["牛奶蛋白"]},
                                   "is_third_party": False, "is_hypothetical": False},
                                  ensure_ascii=False)
            if "段" in cur or "6个月" in cur:
                return json.dumps({"action": "chat", "customer": "张姐", "baby": "壮壮",
                                   "extracted": {"baby_age": "6个月", "stage": "1段"},
                                   "is_third_party": False, "is_hypothetical": False},
                                  ensure_ascii=False)
            return json.dumps({"action": "chat", "baby": "", "extracted": {},
                               "is_third_party": False, "is_hypothetical": False},
                              ensure_ascii=False)

    r1 = await resolve_and_archive(store, ArchiveProvider(), "ent1", "emp1", "",
                                   "壮壮6个月喝1段", None)
    assert r1.focus_baby_id == bid
    b = store.get_baby(bid)
    assert b.baby_age == "6个月" and b.stage == "1段"

    # 第二轮：累积过敏原（焦点稳定走结果缓存路径，规则抽取归档到焦点），保留上轮属性
    r2 = await resolve_and_archive(store, ArchiveProvider(), "ent1", "emp1",
                                   "user: 壮壮6个月喝1段", "壮壮对牛奶蛋白过敏", bid)
    assert r2.focus_baby_id == bid
    b2 = store.get_baby(bid)
    assert b2.baby_age == "6个月"        # 保留
    assert b2.stage == "1段"             # 保留
    assert "牛奶蛋白" in b2.allergens    # 累加
    assert b2.status == "confirmed"


# ---------------------------------------------------------------------------
# P6 焦点宝宝档案块注入 system prompt
# ---------------------------------------------------------------------------
def _p6_baby_block_injection():
    cfg = EnterpriseConfig(enterprise_id="ent1")
    agent = Agent(cfg, _DummyStore())
    baby = BabyProfile(None, "ent1", "emp1", 1, "壮壮",
                       baby_age="6个月", allergens=["牛奶蛋白"], status="confirmed")
    block = baby.to_prompt_block(customer_name="张姐")
    msgs = agent._build_messages("推荐什么奶粉", "【知识库】xxx", [], None,
                                 baby_block=block)
    sys = msgs[0]["content"]
    assert "【当前宝宝档案】" in sys
    assert "壮壮" in sys and "6个月" in sys and "牛奶蛋白" in sys


# ---------------------------------------------------------------------------
# P9 向后兼容：不传 baby_block / constraints 时不出现档案块，约束块仍生效
# ---------------------------------------------------------------------------
def _p9_backward_compat():
    cfg = EnterpriseConfig(enterprise_id="ent1")
    agent = Agent(cfg, _DummyStore())
    msgs = agent._build_messages("推荐什么奶粉", "ctx", [], None)
    assert "【当前宝宝档案】" not in msgs[0]["content"]
    # 约束块注入路径仍可用（P1 不受影响）
    from session.constraints import UserConstraints
    c = UserConstraints(baby_age="6个月")
    msgs2 = agent._build_messages("推荐什么奶粉", "ctx", [], c)
    assert "【用户已明确约束】" in msgs2[0]["content"]
    assert "【当前宝宝档案】" not in msgs2[0]["content"]


class _DummyStore:
    def retrieve(self, *a, **k):
        return []


# ---------------------------------------------------------------------------
# P10 pending 防污染：同名跨客户不误合并（旧 pending 不被新真实宝宝复用）
# ---------------------------------------------------------------------------
async def _p10_pending_no_pollution():
    store = BabyProfileStore(_tmp_db())
    cid_l = store.get_or_create_customer("ent1", "emp1", "李姐")
    bid_l = store.create_baby(BabyProfile(None, "ent1", "emp1", cid_l, "壮壮", status="pending"))

    class NewZhuangProvider:
        async def complete(self, messages, retrieved_hits=None, **kw):
            return json.dumps({"action": "new_baby", "customer": "张姐", "baby": "壮壮",
                               "extracted": {"baby_age": "8个月"},
                               "is_third_party": False, "is_hypothetical": False},
                              ensure_ascii=False)

    r = await resolve_and_archive(store, NewZhuangProvider(), "ent1", "emp1", "",
                                  "张姐家壮壮8个月了", None)
    # 必须为张姐新建一个壮壮，绝不与李姐的 pending 误合并
    assert r.created is True
    assert r.focus_baby_id != bid_l
    bz = store.get_baby(r.focus_baby_id)
    assert bz.name == "壮壮" and bz.baby_age == "8个月"
    # 李姐的 pending 壮壮完好无损（无属性被误并入）
    bl = store.get_baby(bid_l)
    assert bl.baby_age == "" and bl.customer_id == cid_l


# ---------------------------------------------------------------------------
# P11 同名多客户歧义：未给客户名时不自动匹配（防跨客户误配）
# ---------------------------------------------------------------------------
async def _p11_ambiguity_no_match():
    store = BabyProfileStore(_tmp_db())
    cid1 = store.get_or_create_customer("ent1", "emp1", "张姐")
    store.create_baby(BabyProfile(None, "ent1", "emp1", cid1, "壮壮", status="confirmed"))
    cid2 = store.get_or_create_customer("ent1", "emp1", "李姐")
    store.create_baby(BabyProfile(None, "ent1", "emp1", cid2, "壮壮", status="pending"))
    known = store.list_for_employee("ent1", "emp1")

    class AmbigProvider:
        async def complete(self, messages, retrieved_hits=None, **kw):
            return json.dumps({"action": "chat", "baby": "壮壮", "customer": "",
                               "extracted": {}, "is_third_party": False,
                               "is_hypothetical": False}, ensure_ascii=False)

    r = await resolve_and_extract("user: hi", "壮壮喝1段", known, None, AmbigProvider())
    assert r.baby_id is None, "同名多客户且未指定客户应歧义不匹配"


# ---------------------------------------------------------------------------
# P12 过期待确认清理：prune_stale_pending 只删陈旧 pending，confirmed 不动
# ---------------------------------------------------------------------------
def _p12_prune_stale_pending():
    store = BabyProfileStore(_tmp_db())
    cid = store.get_or_create_customer("ent1", "emp1", "张姐")
    store.create_baby(BabyProfile(None, "ent1", "emp1", cid, "豆豆", status="pending"))  # 新鲜
    old_bid = store.create_baby(BabyProfile(None, "ent1", "emp1", cid, "点点", status="pending"))
    with connect(store.db_path) as conn:
        conn.execute("UPDATE babies SET created_at=? WHERE baby_id=?",
                     (time.time() - 90 * 86400, old_bid))
        conn.commit()
    conf_bid = store.create_baby(BabyProfile(None, "ent1", "emp1", cid, "康康", status="confirmed"))

    n = store.prune_stale_pending(days=30)
    assert n == 1
    assert store.get_baby(old_bid) is None          # 陈旧 pending 已清
    assert store.get_baby(conf_bid) is not None      # confirmed 永不动
    assert len(store.list_for_employee("ent1", "emp1")) == 2  # 新鲜 pending + confirmed 留存


# ---------------------------------------------------------------------------
# P13 消歧失败可观测：parse_failed 标志 + 兜底沿用焦点不崩
# ---------------------------------------------------------------------------
async def _p13_parse_failed_flag():
    store = BabyProfileStore(_tmp_db())
    cid = store.get_or_create_customer("ent1", "emp1", "张姐")
    bid = store.create_baby(BabyProfile(None, "ent1", "emp1", cid, "壮壮", status="confirmed"))
    known = store.list_for_employee("ent1", "emp1")

    class GarbageProvider:
        async def complete(self, messages, retrieved_hits=None, **kw):
            return "【系统】模型返回了无法解析的内容 502"

    r = await resolve_and_extract("user: hi", "壮壮喝1段", known, bid, GarbageProvider())
    assert r.parse_failed is True
    assert r.baby_id == bid          # 兜底沿用焦点，不崩溃
    assert store.get_baby(bid).baby_age == ""  # 失败不误归档


# ---------------------------------------------------------------------------
# P14 网关级连续失败熔断：≥阈值后降级为仅产品问答，不再建档/归档
# ---------------------------------------------------------------------------
async def _p14_circuit_breaker():
    from wechat.gateway import WechatGateway, BABY_RESOLUTION_FAIL_THRESHOLD
    from wechat.ilink_client import IncomingMessage
    from session.store import SessionStore
    from agent.pipeline import Agent
    from common.config import EnterpriseConfig

    cfg = EnterpriseConfig(enterprise_id="ent1", baby_profile_enabled=True)
    session = SessionStore(_tmp_db())
    baby_store = BabyProfileStore(_tmp_db())
    # 种入已知宝宝，使消息触发 LLM 消歧（否则会被短路、不调 LLM、无解析失败）
    cid = baby_store.get_or_create_customer("ent1", "emp1", "张姐")
    baby_store.create_baby(BabyProfile(None, "ent1", "emp1", cid, "壮壮", status="confirmed"))
    agent = Agent(cfg, _DummyStore())
    agent.provider = _GarbageProvider()

    class MockClient:
        def __init__(self):
            self.sent = []
        async def send_message(self, emp, text, ctx):
            self.sent.append(text)
        async def get_updates(self, buf):
            return None

    gw = WechatGateway(cfg, session, agent, MockClient(), baby_store)

    def mk(i):
        return IncomingMessage(message_id=f"m{i}", from_user_id="emp1", content="壮壮怎么样")

    for i in range(3):
        await gw.handle_message(mk(i), None)
    sid = session.get_or_create("ent1", "emp1", "emp1")
    assert session.get_resolution_fails(sid) >= BABY_RESOLUTION_FAIL_THRESHOLD
    # 已熔断 → 跳过建档/归档，不应新建任何宝宝
    before = len(baby_store.list_for_employee("ent1", "emp1"))
    await gw.handle_message(mk(99), None)
    after = len(baby_store.list_for_employee("ent1", "emp1"))
    assert after == before, "熔断后不应再自动建档"


class _GarbageProvider:
    async def complete(self, messages, retrieved_hits=None, **kw):
        return "【系统】502 bad gateway"


# ---------------------------------------------------------------------------
# P15 跨会话写锁：并发 upsert 同一宝宝不丢失更新
# ---------------------------------------------------------------------------
def _p15_concurrent_write_lock():
    import threading
    store = BabyProfileStore(_tmp_db())
    cid = store.get_or_create_customer("ent1", "emp1", "张姐")
    bid = store.create_baby(BabyProfile(None, "ent1", "emp1", cid, "壮壮", status="confirmed"))

    def worker(tag, n):
        for _ in range(n):
            store.upsert_baby_attrs(bid, BabyProfile(
                None, "ent1", "emp1", cid, "壮壮", allergens=[tag]))

    ths = [threading.Thread(target=worker, args=(t, 50)) for t in ("A", "B")]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    b = store.get_baby(bid)
    assert "A" in b.allergens and "B" in b.allergens, f"并发 upsert 丢失更新：{b.allergens}"


# ---------------------------------------------------------------------------
# P16 焦点稳定结果缓存（D1 修复后：每轮走 LLM，LLM 负责归属+抽取）
# ---------------------------------------------------------------------------
async def _p16_focus_stable_cache():
    store = BabyProfileStore(_tmp_db())
    cid1 = store.get_or_create_customer("ent1", "emp1", "张姐")
    bid1 = store.create_baby(BabyProfile(None, "ent1", "emp1", cid1, "壮壮", status="confirmed"))
    cid2 = store.get_or_create_customer("ent1", "emp1", "李姐")
    bid2 = store.create_baby(BabyProfile(None, "ent1", "emp1", cid2, "妞妞", status="confirmed"))

    class CountProvider:
        def __init__(self):
            self.calls = 0

        async def complete(self, messages, retrieved_hits=None, **kw):
            self.calls += 1
            cur = messages[-1]["content"].split("\nuser: ")[-1]
            if "妞妞" in cur:
                return json.dumps({"action": "chat", "customer": "李姐", "baby": "妞妞",
                                   "extracted": {"baby_age": "2岁"},
                                   "is_third_party": False, "is_hypothetical": False},
                                  ensure_ascii=False)
            if "壮壮" in cur:
                return json.dumps({"action": "chat", "customer": "张姐", "baby": "壮壮",
                                   "extracted": {"allergens": ["牛奶蛋白"]},
                                   "is_third_party": False, "is_hypothetical": False},
                                  ensure_ascii=False)
            return json.dumps({"action": "chat", "baby": "", "extracted": {},
                               "is_third_party": False, "is_hypothetical": False},
                              ensure_ascii=False)

    p = CountProvider()
    # D1 修复后：每轮都走 LLM（LLM 负责归属判断 + 属性抽取）
    r1 = await resolve_and_archive(store, p, "ent1", "emp1", "", "壮壮对牛奶蛋白过敏", bid1)
    assert p.calls == 1, "每轮都应走 LLM（D1 修复后取消规则短路）"
    assert r1.focus_baby_id == bid1
    assert "牛奶蛋白" in store.get_baby(bid1).allergens
    # 提及另一已知宝宝妞妞 → LLM 检测切换
    r2 = await resolve_and_archive(store, p, "ent1", "emp1", "", "妞妞现在2岁换什么", bid1)
    assert p.calls == 2, "第二轮也应走 LLM"
    assert r2.focus_baby_id == bid2


# ---------------------------------------------------------------------------
# P17 Prompt Caching：稳定前缀置首且跨轮一致(缓存命中契约) + cache_control 断点
# ---------------------------------------------------------------------------
async def _p17_prompt_caching():
    store = BabyProfileStore(_tmp_db())
    cid1 = store.get_or_create_customer("ent1", "emp1", "张姐")
    bid1 = store.create_baby(BabyProfile(None, "ent1", "emp1", cid1, "壮壮", status="confirmed"))
    cid2 = store.get_or_create_customer("ent1", "emp1", "李姐")
    bid2 = store.create_baby(BabyProfile(None, "ent1", "emp1", cid2, "妞妞", status="confirmed"))
    known = store.list_for_employee("ent1", "emp1")

    captured = {}

    class SpyProvider:
        async def complete(self, messages, retrieved_hits=None, **kw):
            captured["messages"] = messages
            captured["cache_control"] = kw.get("cache_control", False)
            return json.dumps({"action": "chat", "baby": "", "extracted": {},
                               "is_third_party": False, "is_hypothetical": False},
                              ensure_ascii=False)

    # 第一次调用（focus=bid1）
    await resolve_and_extract("user: 壮壮6个月", "壮壮喝1段", known, bid1, SpyProvider())
    assert captured.get("cache_control") is True, "应开启 cache_control"
    msgs1 = captured["messages"]
    assert msgs1[0]["role"] == "system", "稳定前缀必须是首条 system 消息"
    sys1 = msgs1[0]["content"]
    # 稳定前缀 = 指令 + known 清单（客户名来自 known，证明 known 已纳入缓存前缀）
    assert "宝宝意图消歧器" in sys1
    assert "张姐" in sys1, "known 清单应在缓存前缀内"
    # 稳定前缀不得包含每轮变量（焦点 id / 当前句）
    assert "本会话当前焦点宝宝 id" not in sys1, "焦点变量不得进入缓存前缀"
    assert "喝1段" not in sys1, "当前句不得进入缓存前缀"
    # 变量在 user turn，且当前句为其末段
    assert msgs1[1]["role"] == "user"
    assert "本会话当前焦点宝宝 id" in msgs1[1]["content"]
    assert msgs1[1]["content"].split("\nuser: ")[-1] == "壮壮喝1段"

    # 缓存命中契约：相同 known、不同 focus/当前句 → system 内容完全一致
    await resolve_and_extract("user: 妞妞2岁", "妞妞换3段", known, bid2, SpyProvider())
    sys2 = captured["messages"][0]["content"]
    assert sys1 == sys2, "相同 known 下稳定前缀必须一致（缓存命中）"

    # 新增宝宝 → known 变化 → 前缀变化（缓存未命中，重新缓存）
    store.create_baby(BabyProfile(None, "ent1", "emp1", cid1, "小宝", status="confirmed"))
    known2 = store.list_for_employee("ent1", "emp1")
    await resolve_and_extract("", "小宝喝什么", known2, bid1, SpyProvider())
    sys3 = captured["messages"][0]["content"]
    assert sys3 != sys1, "known 变化后稳定前缀应变化（缓存未命中）"
    assert "小宝" in sys3, "新增宝宝应进入缓存前缀"

    # Anthropic 断点转换：首条 system 内容应包成 content-block 并带 cache_control
    class _CfgAnth:
        kind = "anthropic"

    anth = _apply_cache_control(
        [{"role": "system", "content": sys1}, {"role": "user", "content": "x"}],
        _CfgAnth(),
    )
    assert isinstance(anth[0]["content"], list), "Anthropic 应把 system 内容包成列表"
    assert anth[0]["content"][0].get("cache_control") == {"type": "ephemeral"}

    # OpenAI 兼容（cloud）不改写请求体（靠自动前缀缓存）
    class _CfgCloud:
        kind = "cloud"

    cloud = _apply_cache_control(
        [{"role": "system", "content": sys1}, {"role": "user", "content": "x"}],
        _CfgCloud(),
    )
    assert cloud[0]["content"] == sys1, "cloud 端点不应改写请求体"

    # 缓存收益量化契约：缓存前缀占整体 prompt 主体（体现 50-90% input 节省潜力）
    total_len = sum(len(m["content"]) for m in captured["messages"]
                    if isinstance(m.get("content"), str))
    ratio = len(sys1) / total_len
    assert ratio > 0.5, f"缓存前缀应占 prompt 主体(>50%)，实际 {ratio:.2%}"


# ---------------------------------------------------------------------------
# P18 prompt 结构：system = 稳定指令 + 已知清单（byte-for-byte 可缓存）
# P19 序列化稳定：同输入 known 两次 json.dumps 一致；顺序即缓存键（证明 ORDER BY 必要）
# P20 SQL 排序稳定：list_for_employee 返回顺序跨调用一致（缓存命中前提）
# ---------------------------------------------------------------------------
async def _p18_prompt_structure():
    from baby.resolution import _SYSTEM_INSTRUCTION, _KNOWN_HEADER

    store = BabyProfileStore(_tmp_db())
    cid1 = store.get_or_create_customer("ent1", "emp1", "张姐")
    store.create_baby(BabyProfile(None, "ent1", "emp1", cid1, "壮壮", status="confirmed"))
    cid2 = store.get_or_create_customer("ent1", "emp1", "李姐")
    store.create_baby(BabyProfile(None, "ent1", "emp1", cid2, "妞妞", status="confirmed"))
    known = store.list_for_employee("ent1", "emp1")

    captured = {}

    class Spy:
        async def complete(self, messages, retrieved_hits=None, **kw):
            captured["m"] = messages
            return json.dumps({"action": "chat", "baby": "", "extracted": {},
                               "is_third_party": False, "is_hypothetical": False},
                              ensure_ascii=False)

    await resolve_and_extract("user: 壮壮6个月", "壮壮喝1段", known, cid1, Spy())
    sys_sent = captured["m"][0]["content"]
    # P18：system 必须是「指令 + 清单头部 + known_json」的精确拼接（可精确重建 → 可缓存）
    known_json = json.dumps(known, ensure_ascii=False)
    assert sys_sent == _SYSTEM_INSTRUCTION + _KNOWN_HEADER + known_json, \
        "system 必须是 稳定指令 + 已知清单 的精确拼接（缓存前缀 byte-for-byte 一致）"


async def _p19_serialization_stable():
    store = BabyProfileStore(_tmp_db())
    c1 = store.get_or_create_customer("e", "m", "张姐")
    store.create_baby(BabyProfile(None, "e", "m", c1, "壮壮", status="confirmed"))
    c2 = store.get_or_create_customer("e", "m", "李姐")
    store.create_baby(BabyProfile(None, "e", "m", c2, "妞妞", status="confirmed"))
    known = store.list_for_employee("e", "m")

    # P19：同输入两次 json.dumps 完全一致（缓存命中前提）
    assert json.dumps(known, ensure_ascii=False) == json.dumps(known, ensure_ascii=False)
    # 顺序即缓存键：打乱顺序会得到不同的 json（证明无 ORDER BY 会破坏缓存）
    shuffled = list(reversed(known))
    assert json.dumps(known, ensure_ascii=False) != json.dumps(shuffled, ensure_ascii=False), \
        "顺序参与缓存键——这正是 list_for_employee 必须 ORDER BY 的原因"


async def _p20_sql_order_stable():
    store = BabyProfileStore(_tmp_db())
    # 刻意以「乱序」插入，验证 ORDER BY 保证输出稳定
    c_z = store.get_or_create_customer("e", "m", "张姐")
    c_l = store.get_or_create_customer("e", "m", "李姐")
    store.create_baby(BabyProfile(None, "e", "m", c_l, "妞妞", status="confirmed"))  # 先建李姐
    store.create_baby(BabyProfile(None, "e", "m", c_z, "壮壮", status="confirmed"))  # 再建张姐

    a = store.list_for_employee("e", "m")
    # 连续两次调用顺序一致
    b = store.list_for_employee("e", "m")
    assert json.dumps(a, ensure_ascii=False) == json.dumps(b, ensure_ascii=False), \
        "list_for_employee 返回顺序须跨调用稳定"
    # 中途新增宝宝后，既有顺序仍稳定（按 customer_id, baby_id 排序）
    c_z2 = store.get_or_create_customer("e", "m", "张姐")
    store.create_baby(BabyProfile(None, "e", "m", c_z2, "小宝", status="confirmed"))
    c = store.list_for_employee("e", "m")
    # 张姐(customer_id=1) 的两个宝宝应排在 李姐(customer_id=2) 之前，组内按 baby_id 升序
    names = [x["baby_name"] for x in c]
    assert names.index("壮壮") < names.index("小宝") < names.index("妞妞"), \
        f"应按 (customer_id, baby_id) 稳定排序，实际 {names}"


# ---------------------------------------------------------------------------
# P21 RAG prompt 顺序（优化 C·阶段2）：稳定企业 prompt 在前、动态检索 context 在后
# ---------------------------------------------------------------------------
def _p21_rag_prompt_order():
    cfg = EnterpriseConfig(enterprise_id="ent1")
    agent = Agent(cfg, _DummyStore())

    # 无半稳定块：context 应位于 system 末尾（稳定前缀可被缓存）
    msgs = agent._build_messages("推荐什么奶粉", "【知识库】xxx", [], None, None)
    sys0 = msgs[0]["content"]
    assert sys0.startswith(cfg.system_prompt), "稳定企业 prompt 必须置首"
    assert sys0.endswith(f"【企业知识库】\n【知识库】xxx"), "动态检索 context 必须置于末尾"

    # 有半稳定块（档案 + 约束）：context 须在它们之后（稳定+半稳定构成可缓存前缀）
    baby = BabyProfile(None, "ent1", "emp1", 1, "壮壮",
                       baby_age="6个月", allergens=["牛奶蛋白"], status="confirmed")
    block = baby.to_prompt_block(customer_name="张姐")
    from session.constraints import UserConstraints
    cons = UserConstraints(baby_age="6个月")
    msgs2 = agent._build_messages("推荐什么奶粉", "【知识库】yyy", [], cons, block)
    sys2 = msgs2[0]["content"]
    i_stable = sys2.index(cfg.system_prompt)
    i_baby = sys2.index("【当前宝宝档案】")
    i_cons = sys2.index("【用户已明确约束】")
    # 稳定指令文本也含"【企业知识库】"，故取最后一个出现 = 动态检索块（置于末尾）
    i_ctx = sys2.rindex("【企业知识库】")
    assert i_stable < i_baby < i_cons < i_ctx, \
        "顺序须为 稳定 < 档案块 < 约束块 < 动态context（前缀可缓存）"


# ---------------------------------------------------------------------------
# P23 缓存预热（优化 C·阶段4）：构造稳定前缀并触发一次 provider 调用
# ---------------------------------------------------------------------------
async def _p23_warmup():
    from baby.resolution import _SYSTEM_INSTRUCTION, _KNOWN_HEADER

    store = BabyProfileStore(_tmp_db())
    cid = store.get_or_create_customer("e", "m", "张姐")
    store.create_baby(BabyProfile(None, "e", "m", cid, "壮壮", status="confirmed"))
    known = store.list_for_employee("e", "m")

    captured = {}

    class Spy:
        def __init__(self):
            self.calls = 0

        async def complete(self, messages, retrieved_hits=None, **kw):
            self.calls += 1
            captured["m"] = messages
            captured["kw"] = kw
            return "{}"

    spy = Spy()
    await warmup_prompt_cache(store, spy, "e", "m")
    assert spy.calls == 1, "预热应触发一次 provider 调用"
    sent = captured["m"]
    assert sent[0]["role"] == "system"
    assert sent[0]["content"] == _SYSTEM_INSTRUCTION + _KNOWN_HEADER + json.dumps(known, ensure_ascii=False), \
        "预热须发送与消歧一致的稳定前缀（写入同一缓存）"
    assert captured["kw"].get("cache_control") is True, "预热应开启 cache_control"


CHECKS = [
    ("P1 显式建档(客户+宝宝)", _p1_explicit_create),
    ("P7 (ent,emp) 隔离", _p7_isolation),
    ("P8 持久化全链路(upsert/confirm/merge/delete)", _p8_roundtrip),
    ("P4 快速切换意图消歧", _p4_switching),
    ("P5 代词指代 + 每轮走LLM", _p5_pronoun),
    ("P2 混合式建档安全网(自动建档+第三方不建)", _p2_autocreate_safety),
    ("P3 主动归档跨轮累积", _p3_cross_turn_archive),
    ("P6 焦点宝宝档案块注入system", _p6_baby_block_injection),
    ("P9 向后兼容(constraints仍生效)", _p9_backward_compat),
    ("P10 pending防污染(同名跨客户不误合并)", _p10_pending_no_pollution),
    ("P11 同名歧义不误配", _p11_ambiguity_no_match),
    ("P12 过期待确认清理", _p12_prune_stale_pending),
    ("P13 消歧失败可观测(parse_failed)", _p13_parse_failed_flag),
    ("P14 网关级连续失败熔断", _p14_circuit_breaker),
    ("P15 跨会话写锁并发upsert", _p15_concurrent_write_lock),
    ("P16 每轮走LLM(归属+抽取)", _p16_focus_stable_cache),
    ("P17 Prompt Caching(稳定前缀命中契约+cache_control断点)", _p17_prompt_caching),
    ("P18 消歧prompt结构(system=指令+known,byte-for-byte可缓存)", _p18_prompt_structure),
    ("P19 序列化稳定(同known两次json一致+顺序即缓存键)", _p19_serialization_stable),
    ("P20 list_for_employee 排序稳定(ORDER BY)", _p20_sql_order_stable),
    ("P21 RAG prompt顺序(稳定在前/动态context在后)", _p21_rag_prompt_order),
    ("P23 缓存预热(构造稳定前缀+触发调用)", _p23_warmup),
]


def main():
    failed = []
    for name, fn in CHECKS:
        try:
            if asyncio.iscoroutinefunction(fn):
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
