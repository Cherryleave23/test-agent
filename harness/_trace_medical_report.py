"""模拟成人医学检验报告到达 agent 的行为流程追踪。"""
import sys, os, re
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

from session.constraints import extract_constraints, UserConstraints
from baby.resolution import (
    focus_is_stable, _rule_extract, _extract_birth_date,
    _extract_gestational_weeks, _extract_medical_history, _extract_feeding_history,
    _BABY_SIGNALS, _THIRD_PARTY_HINTS, _PRONOUN_SIGNALS,
)

# 模拟用户输入：成人医学检验报告
MEDICAL_REPORT = """姓名：张XX                    性别：男
年龄：52岁                        申请科室：消化内科
申请医师：李XX                    采血日期：2026-07-21 09:15
样本类型：静脉血（促凝管+EDTA抗凝管）
----------------------------------------------------------------

一、胃黏膜血清学指标（化学发光法）

项目名称              英文缩写    结果      参考区间        单位      提示
-------------------------------------------------------------------------------
胃蛋白酶原I           PG I        32.4 ↓   70.0 - 165.0   ng/mL     偏低
胃蛋白酶原II          PG II       8.7       3.0 - 15.0     ng/mL     正常
PG I / PG II 比值     PGR         3.72 ↓   > 7.5         比值       显著偏低
胃泌素-17             G-17        1.5 ↓     1.0 - 15.0    pmol/L    偏低（近临界）
幽门螺杆菌抗体        Hp-IgG      阳性（+）  阴性          -         现症或既往感染

二、血常规（鉴别贫血）

项目名称              结果        参考区间          单位        提示
-------------------------------------------------------------------------------
血红蛋白              HGB 108 ↓   130 - 175         g/L        轻度贫血
红细胞计数            RBC 3.8 ↓   4.3 - 5.8         10^12/L    偏低
平均红细胞体积        MCV 98      82 - 100          fL         正常
白细胞计数            WBC 5.6     3.5 - 9.5         10^9/L     正常
血小板计数            PLT 220     100 - 300         10^9/L     正常

报告医师：王XX
审核医师：刘XX
报告日期：2026-07-21 16:30"""

print("=" * 70)
print("阶段2：用户约束抽取（extract_constraints）")
print("=" * 70)
uc = extract_constraints(MEDICAL_REPORT)
print(f"  baby_age    = '{uc.baby_age}'")
print(f"  stage       = '{uc.stage}'")
print(f"  allergens   = {uc.allergens}")
print(f"  budget      = {uc.budget}")
print(f"  category    = '{uc.category}'")
print(f"  notes       = '{uc.notes}'")
if uc.baby_age == "52岁":
    print("  ❌ FALSE POSITIVE: 成人年龄 '52岁' 被抽取为宝宝月龄！")

print()
print("=" * 70)
print("阶段3-1：focus_is_stable 判定（假设焦点已设为宝宝A）")
print("=" * 70)
# 模拟 known 列表（宝宝A 已建档）
known = [
    {"baby_id": 1, "baby_name": "小宝", "customer_name": "王芳", "customer_id": 1},
]
focus_baby_id = 1
msg = MEDICAL_REPORT

has_baby_signal = bool(_BABY_SIGNALS.search(msg))
has_third_party = bool(_THIRD_PARTY_HINTS.search(msg))
has_pronoun = bool(_PRONOUN_SIGNALS.search(msg))
print(f"  _BABY_SIGNALS 命中: {has_baby_signal}")
print(f"  _THIRD_PARTY_HINTS 命中: {has_third_party}")
print(f"  _PRONOUN_SIGNALS 命中: {has_pronoun}")

stable = focus_is_stable(known, focus_baby_id, msg)
print(f"  focus_is_stable 返回: {stable}")
if stable:
    print("  ❌ 焦点判定为稳定 → 将走规则短路路径，不调 LLM！")

print()
print("=" * 70)
print("阶段3-2：_rule_extract 规则抽取（焦点稳定路径）")
print("=" * 70)
birth_date = _extract_birth_date(msg)
print(f"  birth_date       = '{birth_date}'")
if birth_date == "2026-07-21":
    print("  ❌ FALSE POSITIVE: 采血日期被抽取为宝宝出生日期！")

gw = _extract_gestational_weeks(msg)
print(f"  gestational_weeks = {gw}")

mh = _extract_medical_history(msg)
print(f"  medical_history  = {mh}")
if "贫血" in mh:
    print("  ❌ FALSE POSITIVE: 成人贫血被抽取为宝宝医疗史！")

fh = _extract_feeding_history(msg)
print(f"  feeding_history  = {fh}")

extracted = _rule_extract(msg)
print(f"\n  _rule_extract 完整结果:")
print(f"    baby_age         = '{extracted.baby_age}'")
print(f"    birth_date       = '{extracted.birth_date}'")
print(f"    medical_history  = {extracted.medical_history}")
print(f"    is_empty_attr    = {extracted.is_empty_attr()}")

if not extracted.is_empty_attr():
    print("  ❌ is_empty_attr=False → store.upsert_baby_attrs 将被调用！")
    print("  ❌ 成人数据将写入宝宝A的档案！")

print()
print("=" * 70)
print("阶段4：检索查询融合（_enrich_query）")
print("=" * 70)
# 模拟被污染的宝宝档案
from baby.models import BabyProfile
contaminated_baby = BabyProfile(
    baby_id=1, enterprise_id="ent1", employee_id="emp1", customer_id=1,
    name="小宝", baby_age="52岁",  # 被污染
    birth_date="2026-07-21",  # 被污染
    medical_history=["贫血"],  # 被污染
    status="confirmed",
)
# 原始宝宝档案（假设有慢性疾病）
original_baby = BabyProfile(
    baby_id=1, enterprise_id="ent1", employee_id="emp1", customer_id=1,
    name="小宝", baby_age="8个月",
    birth_date="2025-11-15",
    gestational_weeks=35,
    medical_history=["早产35周", "牛奶蛋白过敏"],
    feeding_history=["深度水解奶粉"],
    status="confirmed",
)

from agent.pipeline import Agent
# 不需要真实 store，直接调 _enrich_query
class FakeAgent:
    def _enrich_query(self, query, baby_profile=None):
        if baby_profile is None:
            return query
        parts = [query]
        bp = baby_profile
        if bp.baby_age: parts.append(bp.baby_age)
        if bp.stage: parts.append(bp.stage)
        if bp.allergens: parts.extend(bp.allergens)
        if bp.medical_history: parts.extend(bp.medical_history)
        if bp.feeding_history: parts.extend(bp.feeding_history)
        if bp.birth_date: parts.append(bp.birth_date)
        return " ".join(parts)

fa = FakeAgent()
# 模拟用户后续提问（如"该吃什么奶粉"）
query = "该吃什么奶粉"
enriched_clean = fa._enrich_query(query, original_baby)
enriched_contaminated = fa._enrich_query(query, contaminated_baby)
print(f"  原始查询: {query}")
print(f"  干净档案增强: {enriched_clean}")
print(f"  污染档案增强: {enriched_contaminated}")
print("  ❌ 污染后的检索查询含 '52岁' '贫血' '2026-07-21'，KB 检索被严重干扰！")

print()
print("=" * 70)
print("总结：数据污染链路")
print("=" * 70)
print("""
  成人检验报告
    ↓
  focus_is_stable → True（无宝宝信号，判定焦点稳定）
    ↓
  _rule_extract 抽取到:
    baby_age = "52岁"        ← 成人年龄
    birth_date = "2026-07-21" ← 采血日期
    medical_history = ["贫血"] ← 成人贫血
    ↓
  upsert_baby_attrs → 写入宝宝A档案 ❌ 污染！
    ↓
  同时 extract_constraints → baby_age = "52岁" → 注入约束块 ❌ 污染！
    ↓
  _enrich_query 用污染档案增强检索 → KB 检索被干扰 ❌
    ↓
  system prompt 含矛盾信息（宝宝档案显示52岁）→ LLM 回答质量严重下降 ❌
""")
