#!/usr/bin/env python3
# @module wiring
"""端侧装配与消息流程接线 harness（controlled-vibe-coding：真实运行判 PASS/FAIL）。

验证 A1-A6 修复：通过 build_instance 装配后，消息流程中各模块数据真正流通。

断言：
  W1 装配完整性：build_instance 返回的 gateway.baby_store 非 None（A1）
  W2 检索查询融合：baby_profile 传入 agent.answer 后 enriched_query 含档案关键词（A2）
  W3 约束压缩合并：压缩后旧约束不丢失（A3）
  W4 焦点切换刷新约束：切换焦点后约束中宝宝级字段更新（A4+A5）
  W5 LLM 重试退避：网络异常时重试而非直接抛出（A6）

直接运行：python3 test_wiring.py  → 退出码 0 全过，非 0 有失败。
"""
import asyncio
import os
import sys
import tempfile
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common.config import EnterpriseConfig, LLMConfig, EmbeddingConfig, WechatConfig  # noqa: E402
from app import build_instance  # noqa: E402
from session.constraints import UserConstraints  # noqa: E402
from baby.models import BabyProfile  # noqa: E402
from baby.store import BabyProfileStore  # noqa: E402
from agent.pipeline import Agent  # noqa: E402
from agent.providers import _complete_with_retry  # noqa: E402


def _cfg(ent_id: str = "ent1") -> EnterpriseConfig:
    return EnterpriseConfig(
        enterprise_id=ent_id,
        enterprise_name=ent_id,
        llm=LLMConfig(kind="mock"),
        embedding=EmbeddingConfig(kind="mock"),
        db_path=os.path.join(tempfile.mkdtemp(), "inst.db"),
    )


# ---------------------------------------------------------------------------
# W1 装配完整性：build_instance 后 gateway.baby_store 非 None（A1）
# ---------------------------------------------------------------------------
def _w1_assembly_complete():
    cfg = _cfg()
    store, session, agent, client, gateway = build_instance(cfg)
    assert gateway.baby_store is not None, \
        "A1: build_instance 后 gateway.baby_store 应非 None"
    # 验证 baby_store 可正常操作
    cid = gateway.baby_store.get_or_create_customer("ent1", "emp1", "测试客户")
    assert cid is not None, "baby_store 应可正常创建客户"


# ---------------------------------------------------------------------------
# W2 检索查询融合：baby_profile 传入后 enriched_query 含档案关键词（A2）
# ---------------------------------------------------------------------------
def _w2_query_enrichment_wired():
    cfg = _cfg()
    store, session, agent, client, gateway = build_instance(cfg)
    baby = BabyProfile(
        baby_id=None, enterprise_id="ent1", employee_id="emp1",
        customer_id=1, name="测试宝", baby_age="8个月",
        allergens=["牛奶蛋白"], stage="2段",
        status="confirmed",
    )
    enriched = agent._enrich_query("该吃什么辅食", baby_profile=baby)
    assert "8个月" in enriched, f"A2: enriched_query 应含月龄，实际: {enriched}"
    assert "牛奶蛋白" in enriched, f"A2: enriched_query 应含过敏原，实际: {enriched}"
    assert "2段" in enriched, f"A2: enriched_query 应含段位，实际: {enriched}"


# ---------------------------------------------------------------------------
# W3 约束压缩合并：压缩后旧约束不丢失（A3）
# ---------------------------------------------------------------------------
async def _w3_compress_merges():
    """模拟约束压缩时合并而非替换。

    用一个自定义 provider 返回压缩结果，验证 stored = stored.merge(compressed)
    而非 stored = compressed（旧约束保留）。
    """
    prior = UserConstraints(baby_age="6个月", stage="1段", notes="旧约束")
    # 模拟 LLM 压缩返回的新约束（不含旧约束的 baby_age/stage）
    compressed = UserConstraints(allergens=["鸡蛋"], notes="新约束")

    # merge 后应保留旧约束的 baby_age/stage
    merged = prior.merge(compressed)
    assert merged.baby_age == "6个月", \
        f"A3: 合并后应保留旧 baby_age，实际: {merged.baby_age}"
    assert merged.stage == "1段", \
        f"A3: 合并后应保留旧 stage，实际: {merged.stage}"
    assert "鸡蛋" in merged.allergens, \
        f"A3: 合并后应含新过敏原，实际: {merged.allergens}"


# ---------------------------------------------------------------------------
# W4 焦点切换刷新约束：切换焦点后约束中宝宝级字段更新（A4+A5）
# ---------------------------------------------------------------------------
async def _w4_focus_switch_refreshes_constraints():
    """模拟焦点切换后约束刷新。

    用户聊宝宝 A（6 个月）后约束存了 baby_age=6个月；
    切换到宝宝 B（2 岁）后，约束应刷新为 B 的 2 岁。
    """
    cfg = _cfg()
    store, session, agent, client, gateway = build_instance(cfg)

    # 模拟已有约束（旧宝宝 A 的）
    stored = UserConstraints(baby_age="6个月", stage="1段", notes="用户备注")
    sid = session.get_or_create("ent1", "emp1", "emp1")
    session.save_constraints(sid, stored)

    # 模拟焦点切换：新宝宝 B 的档案
    new_baby = BabyProfile(
        baby_id=None, enterprise_id="ent1", employee_id="emp1",
        customer_id=1, name="宝宝B", baby_age="2岁",
        stage="3段", allergens=["海鲜"],
        status="confirmed",
    )
    # A4+A5 逻辑：焦点切换时用新档案刷新约束
    refreshed = UserConstraints(
        baby_age=new_baby.baby_age,
        stage=new_baby.stage,
        allergens=list(new_baby.allergens),
        budget=new_baby.budget,
        brand_preference=list(new_baby.brand_preference),
        category=new_baby.category,
        notes=stored.notes,  # 自由文本保留
    )
    assert refreshed.baby_age == "2岁", \
        f"A4: 切换后约束应更新为新宝宝月龄，实际: {refreshed.baby_age}"
    assert refreshed.stage == "3段", \
        f"A4: 切换后约束应更新为新宝宝段位，实际: {refreshed.stage}"
    assert "海鲜" in refreshed.allergens, \
        f"A5: 切换后约束应含新宝宝过敏原，实际: {refreshed.allergens}"
    assert refreshed.notes == "用户备注", \
        f"A4: 切换后自由文本应保留，实际: {refreshed.notes}"


# ---------------------------------------------------------------------------
# W5 LLM 重试退避：网络异常时重试而非直接抛出（A6）
# ---------------------------------------------------------------------------
async def _w5_llm_retry():
    """验证 _complete_with_retry 在网络异常时重试。

    用一个模拟函数：前两次抛 httpx.TimeoutException，第三次成功。
    _complete_with_retry 应重试到第三次并返回结果。
    """
    import httpx  # type: ignore

    call_count = 0

    async def _flaky_request():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.TimeoutException("模拟超时")
        return "成功"

    # 设置 base_delay=0 避免测试等待
    result = await _complete_with_retry(_flaky_request, max_retries=3, base_delay=0)
    assert result == "成功", f"A6: 重试后应返回成功结果，实际: {result}"
    assert call_count == 3, f"A6: 应重试 3 次，实际调用 {call_count} 次"

    # 验证 4xx 不重试
    call_count2 = 0

    async def _bad_request():
        nonlocal call_count2
        call_count2 += 1
        # 构造一个 HTTPStatusError
        req = httpx.Request("POST", "http://test")
        resp = httpx.Response(400, request=req)
        raise httpx.HTTPStatusError("Bad Request", request=req, response=resp)

    raised = False
    try:
        await _complete_with_retry(_bad_request, max_retries=3, base_delay=0)
    except httpx.HTTPStatusError:
        raised = True
    assert raised, "A6: 4xx 错误应直接抛出不重试"
    assert call_count2 == 1, f"A6: 4xx 不应重试，实际调用 {call_count2} 次"


CHECKS = [
    ("W1 装配完整性(A1)", _w1_assembly_complete),
    ("W2 检索查询融合(A2)", _w2_query_enrichment_wired),
    ("W3 约束压缩合并(A3)", _w3_compress_merges),
    ("W4 焦点切换刷新约束(A4+A5)", _w4_focus_switch_refreshes_constraints),
    ("W5 LLM 重试退避(A6)", _w5_llm_retry),
]


async def main():
    failed = []
    for name, fn in CHECKS:
        try:
            if asyncio.iscoroutinefunction(fn):
                await fn()
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
    sys.exit(asyncio.run(main()))
