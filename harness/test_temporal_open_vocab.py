#!/usr/bin/env python3
# @module baby
"""时序+开放词汇场景 harness（D1 修复后验收）。

验证取消规则短路、每轮走 LLM 后，agent 能正确处理：
  - 相对时间表述（"1年前3岁"）
  - 开放词汇症状（"肚子疼"/"冰淇淋"）
  - 跨轮时序关联（"现在肚子疼"关联到上一轮的宝宝）

断言：
  T1 相对时间+开放词汇：LLM 能抽取规则无法处理的属性
  T2 跨轮症状关联：LLM 能将"现在肚子疼"正确归属到焦点宝宝
  T3 LLM 抽取的属性正确归档（含规则词表外的词汇）

直接运行：python3 test_temporal_open_vocab.py  → 退出码 0 全过，非 0 有失败。
"""
import asyncio
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from baby.models import BabyProfile  # noqa: E402
from baby.store import BabyProfileStore  # noqa: E402
from baby.archive import resolve_and_archive  # noqa: E402


def _tmp_db():
    return os.path.join(tempfile.mkdtemp(), "baby.db")


def _setup_focus_baby(store):
    """创建一个已确认的焦点宝宝（小李，4岁）。"""
    cid = store.get_or_create_customer("ent1", "emp1", "王芳")
    bid = store.create_baby(BabyProfile(
        None, "ent1", "emp1", cid, "小李",
        baby_age="4岁", status="confirmed",
    ))
    return bid


class TemporalProvider:
    """模拟 LLM 处理时序+开放词汇场景。

    LLM 能理解：
    - "1年前3岁" → 现在4岁（时序推算）
    - "3个月前吃3段" → 喂养史
    - "昨天吃了冰淇淋" → 近期饮食
    - "现在肚子疼" → 症状归属焦点宝宝（跨轮关联）
    """
    async def complete(self, messages, **kw):
        cur = messages[-1]["content"].split("\nuser: ")[-1]
        if "1年前" in cur and "3岁" in cur:
            return json.dumps({
                "action": "chat", "customer": "王芳", "baby": "小李",
                "extracted": {
                    "baby_age": "4岁",
                    "feeding_history": ["3个月前吃3段奶粉", "昨天吃了冰淇淋"],
                },
                "is_third_party": False, "is_hypothetical": False,
            }, ensure_ascii=False)
        if "肚子疼" in cur:
            return json.dumps({
                "action": "chat", "customer": "王芳", "baby": "小李",
                "extracted": {
                    "medical_history": ["肚子疼"],
                },
                "is_third_party": False, "is_hypothetical": False,
            }, ensure_ascii=False)
        return json.dumps({
            "action": "chat", "baby": "", "extracted": {},
            "is_third_party": False, "is_hypothetical": False,
        }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# T1 相对时间+开放词汇：LLM 能抽取规则无法处理的属性
# ---------------------------------------------------------------------------
async def _t1_temporal_extraction():
    store = BabyProfileStore(_tmp_db())
    bid = _setup_focus_baby(store)

    msg = "小李1年前3岁，3个月前吃3段奶粉，昨天吃了一点冰淇淋"
    arch = await resolve_and_archive(
        store, TemporalProvider(), "ent1", "emp1", "", msg, bid
    )

    baby = store.get_baby(bid)
    # LLM 理解"1年前3岁"→现在4岁（规则只抓到"3岁"）
    assert baby.baby_age == "4岁", \
        f"T1: LLM 应从'1年前3岁'推算 baby_age='4岁'，实际 '{baby.baby_age}'"
    # LLM 抽取开放词汇喂养史（规则词表无"冰淇淋"）
    assert any("冰淇淋" in fh for fh in baby.feeding_history), \
        f"T1: LLM 应抽取'冰淇淋'到 feeding_history，实际 {baby.feeding_history}"
    assert any("3段" in fh for fh in baby.feeding_history), \
        f"T1: LLM 应抽取'3段'到 feeding_history，实际 {baby.feeding_history}"


# ---------------------------------------------------------------------------
# T2 跨轮症状关联：LLM 能将"现在肚子疼"正确归属到焦点宝宝
# ---------------------------------------------------------------------------
async def _t2_cross_turn_symptom():
    store = BabyProfileStore(_tmp_db())
    bid = _setup_focus_baby(store)

    # 第一轮：发送时序信息
    await resolve_and_archive(
        store, TemporalProvider(), "ent1", "emp1", "",
        "小李1年前3岁，3个月前吃3段奶粉，昨天吃了一点冰淇淋", bid
    )

    # 第二轮：发送"现在肚子疼"（无宝宝信号，但有会话上下文）
    msg2 = "现在肚子疼"
    arch = await resolve_and_archive(
        store, TemporalProvider(), "ent1", "emp1",
        "user: 小李1年前3岁，3个月前吃3段奶粉，昨天吃了一点冰淇淋",
        msg2, bid
    )

    # LLM 应将肚子疼归属到小李（跨轮关联）
    assert arch.focus_baby_id == bid, \
        f"T2: '现在肚子疼'应归属焦点宝宝小李，实际 {arch.focus_baby_id}"
    baby = store.get_baby(bid)
    assert "肚子疼" in baby.medical_history, \
        f"T2: '肚子疼'应归档到 medical_history，实际 {baby.medical_history}"


# ---------------------------------------------------------------------------
# T3 LLM 抽取的属性正确归档（含规则词表外的词汇）
# ---------------------------------------------------------------------------
async def _t3_open_vocab_archive():
    store = BabyProfileStore(_tmp_db())
    bid = _setup_focus_baby(store)

    # 发送一条含规则词表外词汇的消息
    msg = "小李最近有点拉肚子，可能是乳糖不耐受"
    class OpenVocabProvider:
        async def complete(self, messages, **kw):
            return json.dumps({
                "action": "chat", "customer": "王芳", "baby": "小李",
                "extracted": {
                    "medical_history": ["拉肚子", "乳糖不耐受"],
                    "allergens": ["乳糖"],
                },
                "is_third_party": False, "is_hypothetical": False,
            }, ensure_ascii=False)

    await resolve_and_archive(
        store, OpenVocabProvider(), "ent1", "emp1", "", msg, bid
    )

    baby = store.get_baby(bid)
    assert "拉肚子" in baby.medical_history, \
        f"T3: '拉肚子'应归档到 medical_history，实际 {baby.medical_history}"
    assert "乳糖不耐受" in baby.medical_history, \
        f"T3: '乳糖不耐受'应归档到 medical_history，实际 {baby.medical_history}"
    assert "乳糖" in baby.allergens, \
        f"T3: '乳糖'应归档到 allergens，实际 {baby.allergens}"


CHECKS = [
    ("T1 相对时间+开放词汇(LLM抽取)", _t1_temporal_extraction),
    ("T2 跨轮症状关联(肚子疼归属)", _t2_cross_turn_symptom),
    ("T3 开放词汇正确归档(规则词表外)", _t3_open_vocab_archive),
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
