#!/usr/bin/env python3
# @module agent
"""检索查询融合验收（MOD-agent Fix#1）。

验证 pipeline._enrich_query() 和 answer() 的查询增强行为：
  P28: _enrich_query() 融合档案上下文（月龄/段位/过敏/品牌/病史）
  P29: answer() 传 baby_profile 时用增强查询检索（通过 spy store 验证）

直接运行：python3 test_query_enrichment.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
import tempfile
import asyncio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from agent.pipeline import Agent  # noqa: E402
from baby.models import BabyProfile  # noqa: E402
from common.config import EnterpriseConfig, LLMConfig  # noqa: E402


# ---------------------------------------------------------------------------
# P28 _enrich_query 融合档案上下文
# ---------------------------------------------------------------------------
def _p28_enrich_fuses_profile():
    cfg = EnterpriseConfig(enterprise_id="ent_x")
    agent = Agent(cfg, store=None)

    # 无 baby_profile → 原样返回
    assert agent._enrich_query("该吃什么辅食") == "该吃什么辅食"

    # 有 baby_profile → 融合关键字段
    bp = BabyProfile(
        baby_id=1, enterprise_id="e", employee_id="m", customer_id=1, name="共青二宝",
        baby_age="14个月", stage="3段",
        allergens=["牛奶蛋白"],
        brand_preference=["合生元派星"],
        category="奶粉",
        medical_history=["早产35周", "出生5.18斤"],
        feeding_history=["混合喂养→纯奶粉"],
        status="confirmed",
    )
    enriched = agent._enrich_query("该吃什么辅食", bp)
    assert "该吃什么辅食" in enriched
    assert "14个月" in enriched, f"缺 baby_age：{enriched}"
    assert "3段" in enriched, f"缺 stage：{enriched}"
    assert "牛奶蛋白" in enriched, f"缺 allergens：{enriched}"
    assert "合生元派星" in enriched, f"缺 brand：{enriched}"
    assert "早产35周" in enriched, f"缺 medical_history：{enriched}"
    assert "混合喂养→纯奶粉" in enriched, f"缺 feeding_history：{enriched}"


def _p28b_enrich_health_notes_fallback():
    """无结构化字段时 health_notes 作为兜底"""
    cfg = EnterpriseConfig(enterprise_id="ent_x")
    agent = Agent(cfg, store=None)
    bp = BabyProfile(
        baby_id=1, enterprise_id="e", employee_id="m", customer_id=1, name="旧宝",
        baby_age="6个月",
        health_notes="旧格式备注",
        status="confirmed",
    )
    enriched = agent._enrich_query("推荐奶粉", bp)
    assert "6个月" in enriched
    assert "旧格式备注" in enriched, f"缺 health_notes 兜底：{enriched}"


def _p28c_enrich_no_dup_health_notes():
    """有结构化字段时不重复加 health_notes"""
    cfg = EnterpriseConfig(enterprise_id="ent_x")
    agent = Agent(cfg, store=None)
    bp = BabyProfile(
        baby_id=1, enterprise_id="e", employee_id="m", customer_id=1, name="宝",
        baby_age="14个月",
        medical_history=["早产35周"],
        feeding_history=["混合喂养"],
        health_notes="旧格式备注（应被忽略）",
        status="confirmed",
    )
    enriched = agent._enrich_query("辅食", bp)
    assert "早产35周" in enriched
    assert "旧格式备注" not in enriched, "有结构化字段时不应重复加 health_notes"


# ---------------------------------------------------------------------------
# P29 answer() 传 baby_profile 时用增强查询检索
# ---------------------------------------------------------------------------
class _SpyStore:
    """记录传给 retrieve 的查询，返回空结果（无需真实 KB）。"""
    def __init__(self):
        self.captured_query = ""

    def retrieve(self, query, ent_id, top_k=5):
        self.captured_query = query
        return []  # 空命中，answer 会走防幻觉路径


class _MockProvider:
    async def complete(self, messages, retrieved_hits=None, **kw):
        return "mock 回复"


async def _p29_answer_uses_enriched_query():
    cfg = EnterpriseConfig(
        enterprise_id="ent_x",
        llm=LLMConfig(kind="mock"),
    )
    spy_store = _SpyStore()
    agent = Agent(cfg, store=spy_store)
    agent.provider = _MockProvider()

    bp = BabyProfile(
        baby_id=1, enterprise_id="e", employee_id="m", customer_id=1, name="共青二宝",
        baby_age="14个月", stage="3段",
        medical_history=["早产35周"],
        status="confirmed",
    )
    await agent.answer("该吃什么辅食", baby_profile=bp)

    assert "14个月" in spy_store.captured_query, \
        f"retrieve 应收到含 14个月 的增强查询，实际：{spy_store.captured_query}"
    assert "早产35周" in spy_store.captured_query, \
        f"retrieve 应收到含 早产35周 的增强查询，实际：{spy_store.captured_query}"
    assert "3段" in spy_store.captured_query, \
        f"retrieve 应收到含 3段 的增强查询，实际：{spy_store.captured_query}"


async def _p29b_answer_without_profile_uses_raw_query():
    """不传 baby_profile 时用原始查询（向后兼容）"""
    cfg = EnterpriseConfig(
        enterprise_id="ent_x",
        llm=LLMConfig(kind="mock"),
    )
    spy_store = _SpyStore()
    agent = Agent(cfg, store=spy_store)
    agent.provider = _MockProvider()

    await agent.answer("该吃什么辅食")

    assert spy_store.captured_query == "该吃什么辅食", \
        f"无 baby_profile 时应原样传查询，实际：{spy_store.captured_query}"


CHECKS = [
    ("P28 _enrich_query 融合档案", _p28_enrich_fuses_profile),
    ("P28b health_notes 兜底", _p28b_enrich_health_notes_fallback),
    ("P28c 结构化时不重复加 health_notes", _p28c_enrich_no_dup_health_notes),
    ("P29 answer 用增强查询检索", _p29_answer_uses_enriched_query),
    ("P29b 无 profile 时原样查询", _p29b_answer_without_profile_uses_raw_query),
]


def main():
    failed = []
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for name, fn in CHECKS:
        try:
            if asyncio.iscoroutinefunction(fn):
                loop.run_until_complete(fn())
            else:
                fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {name}: 异常 {type(e).__name__}: {e}")
            failed.append(name)
    loop.close()
    print(f"=== Summary: {len(CHECKS) - len(failed)}/{len(CHECKS)} passed ===")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("RESULT: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
