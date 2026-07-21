#!/usr/bin/env python3
# @module baby
"""跨上下文污染防护 harness（controlled-vibe-coding：真实运行判 PASS/FAIL）。

验证 C1+C2 修复：无宝宝信号的消息不归档到焦点宝宝 + 规则抽取合理性校验。

断言：
  X1 成人检验报告不污染焦点宝宝档案（C1：focus_is_stable 无信号→False）
  X2 4岁成人报告不污染焦点宝宝档案（C1+C2：无信号→False + 年龄校验兜底）
  X3 无身份信息检查结果不归档但不报错（C1：无信号→空 extracted）
  X4 C2 合理性校验拒绝荒谬数据（52岁、今天日期）
  X5 正常宝宝消息仍正确归档（无回归）

直接运行：python3 test_cross_context_pollution.py  → 退出码 0 全过，非 0 有失败。
"""
import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from baby.models import BabyProfile  # noqa: E402
from baby.store import BabyProfileStore  # noqa: E402
from baby.archive import resolve_and_archive  # noqa: E402
from baby.resolution import (  # noqa: E402
    focus_is_stable, _rule_extract, _validate_extracted,
)


def _tmp_db():
    return os.path.join(tempfile.mkdtemp(), "baby.db")


def _setup_focus_baby(store):
    """创建一个已确认的焦点宝宝（8个月，早产35周）。"""
    cid = store.get_or_create_customer("ent1", "emp1", "王芳")
    bid = store.create_baby(BabyProfile(
        None, "ent1", "emp1", cid, "小宝",
        baby_age="8个月", birth_date="2025-11-15",
        gestational_weeks=35, medical_history=["早产35周"],
        allergens=["牛奶蛋白"], status="confirmed",
    ))
    return bid


# 52岁成人检验报告
ADULT_REPORT_52 = """姓名：张XX  性别：男  年龄：52岁
申请科室：消化内科  采血日期：2026-07-21 09:15
胃蛋白酶原I  PG I  32.4 ↓  70.0-165.0  ng/mL  偏低
幽门螺杆菌抗体  Hp-IgG  阳性（+）  阴性  现症或既往感染
血红蛋白  HGB 108 ↓  130-175  g/L  轻度贫血
报告日期：2026-07-21 16:30"""

# 4岁版本（合理宝宝年龄，但实际是成人的）
ADULT_REPORT_4 = """姓名：张XX  性别：男  年龄：4岁
申请科室：消化内科  采血日期：2026-07-21 09:15
胃蛋白酶原I  PG I  32.4 ↓  70.0-165.0  ng/mL  偏低
血红蛋白  HGB 108 ↓  130-175  g/L  轻度贫血
报告日期：2026-07-21 16:30"""

# 无身份信息检查结果（可能是宝宝的）
NO_ID_REPORT = """血红蛋白  HGB 108 ↓  110-160  g/L  轻度贫血
红细胞计数  RBC 3.8 ↓  4.0-5.5  10^12/L  偏低
维生素D  18 ↓  20-100  ng/mL  偏低"""


class DummyProvider:
    """空 provider（C1 修复后无信号消息不调 LLM）。"""
    async def complete(self, messages, **kw):
        return "{}"


# ---------------------------------------------------------------------------
# X1 成人检验报告（52岁）不污染焦点宝宝档案
# ---------------------------------------------------------------------------
async def _x1_adult_report_no_pollution():
    store = BabyProfileStore(_tmp_db())
    bid = _setup_focus_baby(store)
    known = store.list_for_employee("ent1", "emp1")

    # focus_is_stable 应返回 False（无宝宝信号）
    stable = focus_is_stable(known, bid, ADULT_REPORT_52)
    assert stable is False, \
        f"X1: 成人报告无宝宝信号，focus_is_stable 应返回 False，实际 {stable}"

    # resolve_and_archive 不应污染档案
    arch = await resolve_and_archive(
        store, DummyProvider(), "ent1", "emp1", "", ADULT_REPORT_52, bid
    )

    baby = store.get_baby(bid)
    assert baby.baby_age == "8个月", \
        f"X1: 成人报告不应覆写 baby_age，期望 '8个月'，实际 '{baby.baby_age}'"
    assert baby.birth_date == "2025-11-15", \
        f"X1: 成人报告不应覆写 birth_date，期望 '2025-11-15'，实际 '{baby.birth_date}'"
    assert "贫血" not in baby.medical_history, \
        f"X1: 成人贫血不应追加到 medical_history，实际 {baby.medical_history}"


# ---------------------------------------------------------------------------
# X2 4岁成人报告不污染（C1 拦截 + C2 兜底）
# ---------------------------------------------------------------------------
async def _x2_4years_no_pollution():
    store = BabyProfileStore(_tmp_db())
    bid = _setup_focus_baby(store)
    known = store.list_for_employee("ent1", "emp1")

    stable = focus_is_stable(known, bid, ADULT_REPORT_4)
    assert stable is False, \
        f"X2: 4岁成人报告无宝宝信号，focus_is_stable 应返回 False，实际 {stable}"

    arch = await resolve_and_archive(
        store, DummyProvider(), "ent1", "emp1", "", ADULT_REPORT_4, bid
    )

    baby = store.get_baby(bid)
    assert baby.baby_age == "8个月", \
        f"X2: 4岁报告不应覆写 baby_age，期望 '8个月'，实际 '{baby.baby_age}'"
    assert baby.birth_date == "2025-11-15", \
        f"X2: 采血日期不应覆写 birth_date，期望 '2025-11-15'，实际 '{baby.birth_date}'"


# ---------------------------------------------------------------------------
# X3 无身份信息检查结果不归档但不报错
# ---------------------------------------------------------------------------
async def _x3_no_id_report_no_error():
    store = BabyProfileStore(_tmp_db())
    bid = _setup_focus_baby(store)

    arch = await resolve_and_archive(
        store, DummyProvider(), "ent1", "emp1", "", NO_ID_REPORT, bid
    )

    # 不报错 + 沿用焦点
    assert arch.focus_baby_id == bid, \
        f"X3: 无身份报告应沿用焦点，期望 {bid}，实际 {arch.focus_baby_id}"
    assert arch.baby is not None, "X3: 应返回焦点宝宝档案供回答层使用"

    # 档案未被污染
    baby = store.get_baby(bid)
    assert baby.baby_age == "8个月", \
        f"X3: 无身份报告不应覆写 baby_age，实际 '{baby.baby_age}'"
    # medical_history 不应被追加 "贫血"（无信号→空 extracted→不归档）
    assert "贫血" not in baby.medical_history, \
        f"X3: 无身份报告不应追加医疗史，实际 {baby.medical_history}"


# ---------------------------------------------------------------------------
# X4 C2 合理性校验拒绝荒谬数据
# ---------------------------------------------------------------------------
def _x4_validation_rejects_absurd():
    # 52岁 → 拒绝
    extracted = BabyProfile(
        baby_id=None, enterprise_id="", employee_id="", customer_id=0,
        name="", baby_age="52岁",
    )
    validated = _validate_extracted(extracted)
    assert validated.baby_age == "", \
        f"X4: 52岁应被拒绝，实际 '{validated.baby_age}'"

    # 未来日期 → 拒绝
    extracted = BabyProfile(
        baby_id=None, enterprise_id="", employee_id="", customer_id=0,
        name="", birth_date="2030-01-01",
    )
    validated = _validate_extracted(extracted)
    assert validated.birth_date == "", \
        f"X4: 未来日期应被拒绝，实际 '{validated.birth_date}'"

    # 太久以前（>6年）→ 拒绝
    extracted = BabyProfile(
        baby_id=None, enterprise_id="", employee_id="", customer_id=0,
        name="", birth_date="2010-01-01",
    )
    validated = _validate_extracted(extracted)
    assert validated.birth_date == "", \
        f"X4: 2010年日期应被拒绝，实际 '{validated.birth_date}'"

    # 4岁 → 通过（合理宝宝年龄）
    extracted = BabyProfile(
        baby_id=None, enterprise_id="", employee_id="", customer_id=0,
        name="", baby_age="4岁",
    )
    validated = _validate_extracted(extracted)
    assert validated.baby_age == "4岁", \
        f"X4: 4岁应通过校验，实际 '{validated.baby_age}'"

    # 正常出生日期 → 通过
    extracted = BabyProfile(
        baby_id=None, enterprise_id="", employee_id="", customer_id=0,
        name="", birth_date="2025-05-21",
    )
    validated = _validate_extracted(extracted)
    assert validated.birth_date == "2025-05-21", \
        f"X4: 正常出生日期应通过，实际 '{validated.birth_date}'"


# ---------------------------------------------------------------------------
# X5 正常宝宝消息仍正确归档（无回归）
# ---------------------------------------------------------------------------
async def _x5_normal_baby_message_still_works():
    store = BabyProfileStore(_tmp_db())
    bid = _setup_focus_baby(store)
    known = store.list_for_employee("ent1", "emp1")

    # 提及焦点宝宝名 + 宝宝信号 → focus_is_stable=True → 规则短路归档
    msg = "小宝最近开始吃辅食了，6个月"
    stable = focus_is_stable(known, bid, msg)
    assert stable is True, \
        f"X5: 提及焦点宝宝名应稳定，实际 {stable}"

    arch = await resolve_and_archive(
        store, DummyProvider(), "ent1", "emp1", "", msg, bid
    )

    baby = store.get_baby(bid)
    # "6个月" 应被抽取并覆写（规则短路路径，已确认归属）
    assert baby.baby_age == "6个月", \
        f"X5: 正常消息应归档 baby_age='6个月'，实际 '{baby.baby_age}'"


CHECKS = [
    ("X1 成人报告(52岁)不污染档案(C1)", _x1_adult_report_no_pollution),
    ("X2 成人报告(4岁)不污染档案(C1+C2)", _x2_4years_no_pollution),
    ("X3 无身份报告不归档不报错(C1)", _x3_no_id_report_no_error),
    ("X4 C2合理性校验拒绝荒谬数据", _x4_validation_rejects_absurd),
    ("X5 正常宝宝消息仍正确归档(无回归)", _x5_normal_baby_message_still_works),
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
