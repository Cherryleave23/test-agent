#!/usr/bin/env python3
# @module baby
"""宝宝/客户档案层（MOD-baby-profile，P2）真实运行验收 harness。

按 CVC：真实运行判 PASS/FAIL，非自述。本文件随实现阶段增量补充检查：
  阶段1（存储）：P1 显式建档 / P7 (ent,emp) 隔离 / P8 持久化全链路(upsert/confirm/merge/delete)
  阶段2（消歧）：P4 快速切换意图消歧 / P5 代词指代
  阶段3（网关）：P2 混合式安全网(自动建档+第三方不建) / P3 主动归档跨轮累积
  阶段4（注入）：P6 焦点宝宝档案块注入 system prompt / P9 向后兼容

直接运行：python3 test_baby_profile.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
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
from common.config import EnterpriseConfig  # noqa: E402


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

    # 短路：无宝宝信号不调 LLM
    class SpyProvider:
        def __init__(self):
            self.calls = 0

        async def complete(self, messages, retrieved_hits=None, **kw):
            self.calls += 1
            return "{}"

    spy = SpyProvider()
    r0 = await resolve_and_extract("user: 壮壮6个月", "今天天气不错", known, bid1, spy)
    assert spy.calls == 0, "无宝宝信号应短路不调 LLM"
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

    # 第二轮：累积过敏原，保留上轮属性与 confirmed 状态
    r2 = await resolve_and_archive(store, ArchiveProvider(), "ent1", "emp1",
                                   "user: 壮壮6个月喝1段", "壮壮对牛奶过敏", bid)
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


CHECKS = [
    ("P1 显式建档(客户+宝宝)", _p1_explicit_create),
    ("P7 (ent,emp) 隔离", _p7_isolation),
    ("P8 持久化全链路(upsert/confirm/merge/delete)", _p8_roundtrip),
    ("P4 快速切换意图消歧", _p4_switching),
    ("P5 代词指代 + 短路不调LLM", _p5_pronoun),
    ("P2 混合式建档安全网(自动建档+第三方不建)", _p2_autocreate_safety),
    ("P3 主动归档跨轮累积", _p3_cross_turn_archive),
    ("P6 焦点宝宝档案块注入system", _p6_baby_block_injection),
    ("P9 向后兼容(constraints仍生效)", _p9_backward_compat),
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
