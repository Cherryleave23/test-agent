#!/usr/bin/env python3
# @module baby
"""终极实战 harness：5 宝宝碎片化信息从零建档 + 交叉提问验收（MOD-baby-profile P3 阶段）。

模拟真实门店场景：空库起步，34 条碎片化信息以随机顺序逐条发送给 agent，
agent 必须自己判断「这是新宝宝→建档」还是「已建档→归档」，并跨轮累积属性。
最后 5 个交叉提问验收档案正确性与回答合理性。

与之前版本的核心区别（用户指正）：
  - 无 Phase 0 预建档案：空库起步，agent 必须从零建档
  - Mock Provider 只返回当前消息提到的单个属性（非全部 GT）：迫使 agent 跨轮累积

验收点：
  P30: 5 个宝宝档案存在且无重复
  P31-P35: 各宝宝基础属性（baby_age/stage/allergens/category/budget）与 ground truth 匹配
  P36: 无跨宝宝串档（A 的过敏原/病史不出现在 B 的档案中）
  P37: 5 个回答非空
  P38: 每个回答引用正确的宝宝名
  P39: 焦点切换正确（提问时焦点切换至对应宝宝）

附加诊断（info 级，不计 PASS/FAIL）：
  - 结构化字段（birth_date/gestational_weeks/medical_history/feeding_history/brand_preference）覆盖率
  - LLM 调用次数 vs 规则抽取（focus_is_stable 短路）次数
  - 各宝宝 LLM 被调用次数

直接运行：python3 test_ultimate_baby_harness.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
import json
import re
import random
import asyncio
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from baby.models import BabyProfile  # noqa: E402
from baby.store import BabyProfileStore  # noqa: E402
from baby.archive import resolve_and_archive  # noqa: E402
from agent.pipeline import Agent  # noqa: E402
from common.config import EnterpriseConfig, LLMConfig  # noqa: E402


# =========================================================================
# Ground Truth：5 个差异化宝宝
# =========================================================================
BABY_GROUND_TRUTHS = {
    "A": {
        "customer": "王芳", "name": "共青二宝",
        "baby_age": "14个月", "birth_date": "2025-05-21",
        "gestational_weeks": 35, "stage": "3段",
        "brand_preference": ["合生元派星"], "category": "奶粉",
        "allergens": [], "budget": None,
        "medical_history": ["早产35周", "出生5.18斤", "新生儿科9天"],
        "feeding_history": ["混合喂养→纯奶粉", "非喷射性吐奶至4-5个月已缓解"],
    },
    "B": {
        "customer": "李娜", "name": "悦悦",
        "baby_age": "20个月", "birth_date": "",
        "gestational_weeks": None, "stage": "3段",
        "brand_preference": ["飞鹤星飞帆"], "category": "奶粉",
        "allergens": ["鸡蛋"], "budget": None,
        "medical_history": [], "feeding_history": [],
    },
    "C": {
        "customer": "张伟", "name": "小石头",
        "baby_age": "18个月", "birth_date": "2024-11-15",
        "gestational_weeks": 32, "stage": "3段",
        "brand_preference": ["a2至初"], "category": "奶粉",
        "allergens": ["牛奶蛋白", "大豆"], "budget": None,
        "medical_history": ["早产32周", "NICU住院15天", "脑室出血", "贫血"],
        "feeding_history": ["深度水解奶粉→氨基酸奶粉→a2至初"],
    },
    "D": {
        "customer": "赵丽", "name": "朵朵",
        "baby_age": "10个月", "birth_date": "",
        "gestational_weeks": None, "stage": "2段",
        "brand_preference": [], "category": "奶粉",
        "allergens": [], "budget": None,
        "medical_history": [], "feeding_history": [],
    },
    "E": {
        "customer": "陈秀", "name": "辰辰",
        "baby_age": "23个月", "birth_date": "",
        "gestational_weeks": None, "stage": "3段",
        "brand_preference": ["爱他美卓萃"], "category": "奶粉",
        "allergens": ["海鲜"], "budget": 400.0,
        "medical_history": [], "feeding_history": [],
    },
}

# =========================================================================
# 碎片化信息：每条 = (消息文本, 归属宝宝key, 该条应抽取的属性dict)
# 每条只含一个事实，agent 必须跨轮累积才能拼出完整档案
# =========================================================================
SHATTERED_INFO = [
    # Baby A（共青二宝）— 10 条
    ("王芳家的宝宝共青二宝出生日期是2025-05-21", "A",
     {"customer": "王芳", "baby": "共青二宝", "birth_date": "2025-05-21"}),
    ("共青二宝这个宝宝现在14个月了", "A",
     {"baby": "共青二宝", "baby_age": "14个月"}),
    ("共青二宝是早产35周的宝宝", "A",
     {"baby": "共青二宝", "gestational_weeks": 35, "medical_history": ["早产35周"]}),
    ("共青二宝出生才5.18斤，宝宝偏小", "A",
     {"baby": "共青二宝", "medical_history": ["出生5.18斤"]}),
    ("共青二宝在新生儿科住了9天，宝宝刚出生时", "A",
     {"baby": "共青二宝", "medical_history": ["新生儿科9天"]}),
    ("共青二宝喝合生元派星3段奶粉", "A",
     {"baby": "共青二宝", "brand_preference": ["合生元派星"], "stage": "3段", "category": "奶粉"}),
    ("共青二宝是奶粉喂养的", "A",
     {"baby": "共青二宝", "category": "奶粉"}),
    ("共青二宝一开始混合喂养后来纯奶粉", "A",
     {"baby": "共青二宝", "feeding_history": ["混合喂养→纯奶粉"]}),
    ("共青二宝之前非喷射性吐奶到4-5个月，现在已经好了", "A",
     {"baby": "共青二宝", "feeding_history": ["非喷射性吐奶至4-5个月已缓解"]}),
    ("共青二宝的客户是王芳", "A",
     {"baby": "共青二宝", "customer": "王芳"}),
    # Baby B（悦悦）— 5 条
    ("李娜家悦悦现在20个月", "B",
     {"customer": "李娜", "baby": "悦悦", "baby_age": "20个月"}),
    ("悦悦喝飞鹤星飞帆3段奶粉", "B",
     {"baby": "悦悦", "brand_preference": ["飞鹤星飞帆"], "stage": "3段", "category": "奶粉"}),
    ("悦悦对鸡蛋过敏", "B",
     {"baby": "悦悦", "allergens": ["鸡蛋"]}),
    ("悦悦是喝奶粉的", "B",
     {"baby": "悦悦", "category": "奶粉"}),
    ("悦悦的客户李娜", "B",
     {"baby": "悦悦", "customer": "李娜"}),
    # Baby C（小石头）— 10 条
    ("张伟家小石头出生2024-11-15，宝宝早产", "C",
     {"customer": "张伟", "baby": "小石头", "birth_date": "2024-11-15"}),
    ("小石头现在18个月", "C",
     {"baby": "小石头", "baby_age": "18个月"}),
    ("小石头是早产32周的宝宝", "C",
     {"baby": "小石头", "gestational_weeks": 32, "medical_history": ["早产32周"]}),
    ("小石头出生后NICU住院15天，宝宝很辛苦", "C",
     {"baby": "小石头", "medical_history": ["NICU住院15天"]}),
    ("小石头有脑室出血，宝宝需要关注", "C",
     {"baby": "小石头", "medical_history": ["脑室出血"]}),
    ("小石头贫血，宝宝需要补铁", "C",
     {"baby": "小石头", "medical_history": ["贫血"]}),
    ("小石头对牛奶蛋白和大豆都过敏", "C",
     {"baby": "小石头", "allergens": ["牛奶蛋白", "大豆"]}),
    ("小石头喝a2至初3段奶粉", "C",
     {"baby": "小石头", "brand_preference": ["a2至初"], "stage": "3段", "category": "奶粉"}),
    ("小石头之前深度水解奶粉后来氨基酸奶粉现在a2至初", "C",
     {"baby": "小石头", "feeding_history": ["深度水解奶粉→氨基酸奶粉→a2至初"]}),
    ("小石头是奶粉喂养", "C",
     {"baby": "小石头", "category": "奶粉"}),
    # Baby D（朵朵）— 3 条
    ("赵丽家朵朵10个月了", "D",
     {"customer": "赵丽", "baby": "朵朵", "baby_age": "10个月"}),
    ("朵朵喝2段奶粉", "D",
     {"baby": "朵朵", "stage": "2段", "category": "奶粉"}),
    ("朵朵的客户赵丽", "D",
     {"baby": "朵朵", "customer": "赵丽"}),
    # Baby E（辰辰）— 6 条
    ("陈秀家辰辰23个月了", "E",
     {"customer": "陈秀", "baby": "辰辰", "baby_age": "23个月"}),
    ("辰辰喝爱他美卓萃3段奶粉", "E",
     {"baby": "辰辰", "brand_preference": ["爱他美卓萃"], "stage": "3段", "category": "奶粉"}),
    ("辰辰对海鲜过敏", "E",
     {"baby": "辰辰", "allergens": ["海鲜"]}),
    ("辰辰预算400元", "E",
     {"baby": "辰辰", "budget": 400.0}),
    ("辰辰是喝奶粉的", "E",
     {"baby": "辰辰", "category": "奶粉"}),
    ("辰辰的客户陈秀", "E",
     {"baby": "辰辰", "customer": "陈秀"}),
]

# 消息 → 属性 查找表（供 Mock Provider 使用）
MSG_TO_ATTRS = {msg: attrs for msg, _key, attrs in SHATTERED_INFO}

# 5 个问题模板
QUESTION_TEMPLATES = [
    "{name}的奶粉建议是什么？",
    "{name}现在适合吃什么辅食？",
    "{name}需要补充维生素吗？",
    "{name}需要额外补充什么营养吗？",
    "{name}的过敏情况需要注意什么？",
]

SEED = 42


# =========================================================================
# Mock Providers
# =========================================================================

def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip()).lower()


def _identify_baby(text: str) -> str:
    """根据文本内容识别宝宝 key。"""
    for key, gt in BABY_GROUND_TRUTHS.items():
        if gt["name"] in text:
            return key
    return None


class DisambigMockProvider:
    """模拟 LLM 消歧器（从零建档版）。

    关键改进（用户指正）：
    - 只返回当前消息提到的单个属性（非全部 GT），迫使 agent 跨轮累积
    - 解析 known 列表判断宝宝是否已建档：已建档→chat（只返 baby 名避免精确匹配失败）；
      未建档→new_baby（返完整 customer+baby）

    注意：focus_is_stable=True 时不会被调用（规则抽取路径）。
    """

    def __init__(self):
        self.call_count = 0
        self.calls_per_baby: dict = {}
        self.short_circuited = 0  # focus_is_stable 短路（未调用 LLM）

    def _parse_known(self, system_msg: str) -> list:
        """从 system prompt 中解析 known 列表。"""
        marker = "已知该员工的客户与宝宝清单（JSON 数组）：\n"
        idx = system_msg.find(marker)
        if idx < 0:
            return []
        json_part = system_msg[idx + len(marker):]
        m = re.search(r'\[.*\]', json_part, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    async def complete(self, messages, retrieved_hits=None, **kw):
        self.call_count += 1
        system_msg = messages[0]["content"]
        user_msg = messages[-1]["content"]
        cur = user_msg.split("\nuser: ")[-1].strip()

        # 解析 known 列表，判断宝宝是否已建档
        known = self._parse_known(system_msg)
        known_baby_names = {_norm(it.get("baby_name", "")) for it in known}

        # 碎片信息阶段：从查找表获取当前消息应抽取的属性
        attrs = MSG_TO_ATTRS.get(cur)
        if attrs is not None:
            baby_name = attrs.get("baby", "")
            customer = attrs.get("customer", "")
            baby_key = _identify_baby(baby_name)
            if baby_key:
                self.calls_per_baby[baby_key] = self.calls_per_baby.get(baby_key, 0) + 1

            # 只返回当前消息提到的属性（非全部 GT）
            extracted = {
                k: v for k, v in attrs.items()
                if k in ("baby_age", "stage", "allergens", "budget",
                         "brand_preference", "category", "birth_date",
                         "gestational_weeks", "medical_history", "feeding_history")
            }

            if _norm(baby_name) in known_baby_names:
                # 已建档 → chat，返回 baby 名 + 消息中提到的客户名（如有）
                # （D2 验证：客户名补充需 Mock 返回 customer 字段）
                return json.dumps({
                    "action": "chat",
                    "customer": customer,
                    "baby": baby_name,
                    "extracted": extracted,
                    "is_third_party": False,
                    "is_hypothetical": False,
                }, ensure_ascii=False)
            else:
                # 新宝宝 → new_baby，返回完整 customer + baby
                return json.dumps({
                    "action": "new_baby",
                    "customer": customer,
                    "baby": baby_name,
                    "extracted": extracted,
                    "is_third_party": False,
                    "is_hypothetical": False,
                }, ensure_ascii=False)

        # 问题阶段：识别宝宝名，返回 chat（宝宝已建档）
        baby_key = _identify_baby(cur)
        if baby_key:
            self.calls_per_baby[baby_key] = self.calls_per_baby.get(baby_key, 0) + 1
            gt = BABY_GROUND_TRUTHS[baby_key]
            return json.dumps({
                "action": "chat",
                "customer": "",
                "baby": gt["name"],
                "extracted": {},
                "is_third_party": False,
                "is_hypothetical": False,
            }, ensure_ascii=False)

        return json.dumps({
            "action": "chat", "baby": "", "extracted": {},
            "is_third_party": False, "is_hypothetical": False,
        }, ensure_ascii=False)


class AnswerMockProvider:
    """模拟 LLM 回答器：根据 system prompt 中的宝宝档案生成回答。"""

    async def complete(self, messages, retrieved_hits=None, **kw):
        system = messages[0]["content"]
        query = messages[-1]["content"]

        for _key, gt in BABY_GROUND_TRUTHS.items():
            if gt["name"] in system:
                parts = [f"针对{gt['name']}的建议："]
                if "奶粉" in query:
                    if gt["brand_preference"]:
                        parts.append(f"建议选择{', '.join(gt['brand_preference'])}"
                                     f"（{gt['stage']}），适合{gt['baby_age']}的宝宝。")
                    else:
                        parts.append(f"建议选择{gt['stage']}奶粉，适合{gt['baby_age']}的宝宝。")
                elif "辅食" in query:
                    parts.append(f"{gt['name']}现在{gt['baby_age']}，")
                    if gt["medical_history"]:
                        parts.append(f"注意有{'; '.join(gt['medical_history'])}，")
                    parts.append("辅食添加需循序渐进。")
                elif "维生素" in query:
                    if gt["medical_history"]:
                        parts.append(f"鉴于{'; '.join(gt['medical_history'])}，建议咨询医生后补充。")
                    else:
                        parts.append("一般建议均衡饮食，必要时补充维生素D。")
                elif "营养" in query:
                    if gt["allergens"]:
                        parts.append(f"注意{gt['name']}对{', '.join(gt['allergens'])}过敏，")
                    parts.append("可选择合适的营养补充剂。")
                elif "过敏" in query:
                    if gt["allergens"]:
                        parts.append(f"{gt['name']}对{', '.join(gt['allergens'])}过敏，"
                                     "需避免接触相关食材。")
                    else:
                        parts.append(f"{gt['name']}目前没有记录过敏原。")
                return "".join(parts)
        return "未找到相关宝宝档案信息。"


class MockKBStore:
    """空 KB store：返回空命中（测试聚焦于档案归档与回答，非 KB 检索）。"""

    def retrieve(self, query, ent_id, top_k=5):
        return []


def _tmp_db():
    return os.path.join(tempfile.mkdtemp(), "ultimate_baby.db")


# =========================================================================
# 模拟运行
# =========================================================================

async def run_simulation():
    """运行完整模拟，返回结果字典。"""
    store = BabyProfileStore(_tmp_db())
    ent, emp = "ent_ultimate", "emp_ultimate"

    # --- Phase 1: 随机打乱 34 条碎片信息，从空库逐条发送 ---
    rng = random.Random(SEED)
    shuffled = list(SHATTERED_INFO)
    rng.shuffle(shuffled)

    disambig = DisambigMockProvider()
    focus_baby_id = None
    history_parts: list = []
    rule_extraction_count = 0
    send_log = []  # 记录每条消息的处理结果

    for msg, baby_key, _attrs in shuffled:
        history_text = "\n".join(history_parts[-5:]) if history_parts else ""
        llm_before = disambig.call_count
        result = await resolve_and_archive(
            store, disambig, ent, emp,
            history_text, msg, focus_baby_id,
        )
        focus_baby_id = result.focus_baby_id
        history_parts.append(f"user: {msg}")
        used_llm = disambig.call_count > llm_before
        if not used_llm:
            rule_extraction_count += 1
        send_log.append({
            "msg": msg, "expected_baby": baby_key,
            "focus_after": result.focus_baby_id,
            "action": result.action,
            "used_llm": used_llm,
            "created": result.created,
        })

    # --- Phase 2: 随机分配 5 个问题给 5 个宝宝，随机顺序提问 ---
    baby_keys = list(BABY_GROUND_TRUTHS.keys())
    rng.shuffle(baby_keys)
    questions = []
    for i, template in enumerate(QUESTION_TEMPLATES):
        key = baby_keys[i]
        gt = BABY_GROUND_TRUTHS[key]
        questions.append((template.format(name=gt["name"]), key))
    rng.shuffle(questions)

    # 回答阶段
    cfg = EnterpriseConfig(enterprise_id=ent, llm=LLMConfig(kind="mock"))
    agent = Agent(cfg, store=MockKBStore())
    agent.provider = AnswerMockProvider()

    answers = []
    for question, expected_key in questions:
        history_text = "\n".join(history_parts[-5:]) if history_parts else ""
        result = await resolve_and_archive(
            store, disambig, ent, emp,
            history_text, question, focus_baby_id,
        )
        focus_baby_id = result.focus_baby_id
        history_parts.append(f"user: {question}")

        baby_profile = store.get_baby(focus_baby_id) if focus_baby_id else None
        baby_block = baby_profile.to_prompt_block() if baby_profile else ""

        answer = await agent.answer(
            question, baby_block=baby_block, baby_profile=baby_profile,
        )
        answers.append((question, expected_key, answer.text, focus_baby_id))

    return {
        "store": store,
        "ent": ent,
        "emp": emp,
        "focus_baby_id": focus_baby_id,
        "disambig": disambig,
        "rule_extraction_count": rule_extraction_count,
        "send_log": send_log,
        "answers": answers,
    }


# =========================================================================
# 验收逻辑 P30-P39
# =========================================================================

def _p30_five_babies_exist(sim):
    """P30: 5 个宝宝档案存在且无重复（从零建档，接受 pending 或 confirmed）。"""
    store = sim["store"]
    babies = store.list_for_employee(sim["ent"], sim["emp"])
    assert len(babies) == 5, f"应有 5 个宝宝，实际 {len(babies)}：{[b['baby_name'] for b in babies]}"
    names = [b["baby_name"] for b in babies]
    assert len(names) == len(set(names)), f"宝宝名重复: {names}"


def _p31_to_p35_basic_fields_match(sim):
    """P31-P35: 各宝宝基础属性匹配 ground truth。"""
    store = sim["store"]
    babies = store.list_for_employee(sim["ent"], sim["emp"])
    name_to_baby = {b["baby_name"]: b for b in babies}

    for key, gt in BABY_GROUND_TRUTHS.items():
        b = name_to_baby.get(gt["name"])
        assert b is not None, f"宝宝 {gt['name']} 不存在"
        baby = store.get_baby(b["baby_id"])

        assert baby.baby_age == gt["baby_age"], \
            f"{gt['name']}: baby_age 期望 '{gt['baby_age']}'，实际 '{baby.baby_age}'"
        assert baby.stage == gt["stage"], \
            f"{gt['name']}: stage 期望 '{gt['stage']}'，实际 '{baby.stage}'"
        assert set(baby.allergens) == set(gt["allergens"]), \
            f"{gt['name']}: allergens 期望 {gt['allergens']}，实际 {baby.allergens}"
        assert baby.category == gt["category"], \
            f"{gt['name']}: category 期望 '{gt['category']}'，实际 '{baby.category}'"
        if gt["budget"] is not None:
            assert baby.budget == gt["budget"], \
                f"{gt['name']}: budget 期望 {gt['budget']}，实际 {baby.budget}"


def _p36_no_cross_contamination(sim):
    """P36: 无跨宝宝串档。"""
    store = sim["store"]
    babies = store.list_for_employee(sim["ent"], sim["emp"])

    for b in babies:
        baby = store.get_baby(b["baby_id"])
        gt = None
        for g in BABY_GROUND_TRUTHS.values():
            if g["name"] == baby.name:
                gt = g
                break
        assert gt is not None, f"宝宝 {baby.name} 无 ground truth"

        for other_gt in BABY_GROUND_TRUTHS.values():
            if other_gt["name"] == baby.name:
                continue
            for allergen in other_gt["allergens"]:
                if allergen in baby.allergens and allergen not in gt["allergens"]:
                    assert False, \
                        f"{baby.name} 的 allergens 含有 {other_gt['name']} 的过敏原 {allergen}"
            for med in other_gt["medical_history"]:
                if med in baby.medical_history and med not in gt["medical_history"]:
                    assert False, \
                        f"{baby.name} 的 medical_history 含有 {other_gt['name']} 的 {med}"
            for brand in other_gt["brand_preference"]:
                if brand in baby.brand_preference and brand not in gt["brand_preference"]:
                    assert False, \
                        f"{baby.name} 的 brand_preference 含有 {other_gt['name']} 的 {brand}"


def _p37_answers_non_empty(sim):
    """P37: 5 个回答非空。"""
    for question, expected_key, answer_text, _ in sim["answers"]:
        assert answer_text and len(answer_text.strip()) > 10, \
            f"回答过短或为空: Q='{question}' A='{answer_text}'"


def _p38_answers_reference_correct_baby(sim):
    """P38: 每个回答引用正确的宝宝名。"""
    for question, expected_key, answer_text, _ in sim["answers"]:
        expected_name = BABY_GROUND_TRUTHS[expected_key]["name"]
        assert expected_name in answer_text, \
            f"回答未引用正确的宝宝 {expected_name}: Q='{question}' A='{answer_text}'"


def _p39_focus_switching_correct(sim):
    """P39: 焦点切换正确（提问后焦点指向对应宝宝）。"""
    store = sim["store"]
    for question, expected_key, _, focus_baby_id in sim["answers"]:
        expected_name = BABY_GROUND_TRUTHS[expected_key]["name"]
        focus_baby = store.get_baby(focus_baby_id) if focus_baby_id else None
        assert focus_baby is not None, \
            f"提问后焦点为空: Q='{question}'"
        assert focus_baby.name == expected_name, \
            f"焦点宝宝 期望 '{expected_name}'，实际 '{focus_baby.name}' (Q='{question}')"


def _report_diagnostics(sim):
    """诊断报告（不计 PASS/FAIL）。"""
    store = sim["store"]
    babies = store.list_for_employee(sim["ent"], sim["emp"])
    disambig = sim["disambig"]

    print("\n--- 诊断：档案列表 ---")
    for b in babies:
        baby = store.get_baby(b["baby_id"])
        print(f"  [{b['status']}] {baby.name} (id={b['baby_id']}, "
              f"客户={b['customer_name']}): "
              f"age={baby.baby_age}, stage={baby.stage}, "
              f"allergens={baby.allergens}, brand={baby.brand_preference}, "
              f"birth_date={baby.birth_date}, gw={baby.gestational_weeks}, "
              f"med={baby.medical_history}, feed={baby.feeding_history}")

    print("\n--- 诊断：结构化字段覆盖率 ---")
    for b in babies:
        baby = store.get_baby(b["baby_id"])
        gt = None
        key = None
        for k, g in BABY_GROUND_TRUTHS.items():
            if g["name"] == baby.name:
                gt = g
                key = k
                break
        if not gt:
            print(f"  {baby.name}: 无 ground truth（可能是错误建档）")
            continue

        struct_fields = {
            "birth_date": (baby.birth_date, gt["birth_date"]),
            "gestational_weeks": (baby.gestational_weeks, gt["gestational_weeks"]),
            "medical_history": (baby.medical_history, gt["medical_history"]),
            "feeding_history": (baby.feeding_history, gt["feeding_history"]),
            "brand_preference": (baby.brand_preference, gt["brand_preference"]),
        }
        matched = 0
        total = 0
        for field, (actual, expected) in struct_fields.items():
            has_gt = bool(expected) or expected == 0
            if not has_gt:
                continue
            total += 1
            if actual == expected:
                matched += 1
            else:
                print(f"  [GAP] {baby.name}.{field}: 期望 {expected}，实际 {actual}")
        llm_calls = disambig.calls_per_baby.get(key, 0)
        coverage = f"{matched}/{total}" if total > 0 else "N/A(无GT)"
        print(f"  {baby.name}: 结构化字段 {coverage} 匹配 | LLM 被调用 {llm_calls} 次")

    total_llm = disambig.call_count
    total_rule = sim["rule_extraction_count"]
    total_msgs = total_llm + total_rule
    print(f"\n--- LLM 调用统计 ---")
    print(f"  总消息数: {total_msgs}")
    if total_msgs:
        print(f"  LLM 调用: {total_llm} 次 ({total_llm * 100 // total_msgs}%)")
        print(f"  规则抽取(focus_is_stable 短路): {total_rule} 次 "
              f"({total_rule * 100 // total_msgs}%)")

    # 诊断：新宝宝未被建档的消息（focus_is_stable 短路导致）
    print(f"\n--- 诊断：规则短路导致新宝宝信息被忽略 ---")
    short_circuited_new_baby = []
    for log in sim["send_log"]:
        if not log["used_llm"]:
            # 检查该消息是否属于一个新宝宝（非焦点宝宝）
            gt = BABY_GROUND_TRUTHS[log["expected_baby"]]
            focus_name = None
            if log["focus_after"]:
                fb = store.get_baby(log["focus_after"])
                if fb:
                    focus_name = fb.name
            if gt["name"] != focus_name:
                short_circuited_new_baby.append(log)
    if short_circuited_new_baby:
        for log in short_circuited_new_baby:
            gt = BABY_GROUND_TRUTHS[log["expected_baby"]]
            print(f"  [IGNORED] 期望归档到 {gt['name']}，但规则短路归到焦点 "
                  f"{store.get_baby(log['focus_after']).name if log['focus_after'] else 'None'}: "
                  f"{log['msg']}")
    else:
        print("  无（所有新宝宝信息都走了 LLM 路径）")


# =========================================================================
# 主入口
# =========================================================================

CHECKS = [
    ("P30 5个宝宝档案存在且无重复", _p30_five_babies_exist),
    ("P31-P35 基础属性匹配ground truth", _p31_to_p35_basic_fields_match),
    ("P36 无跨宝宝串档", _p36_no_cross_contamination),
    ("P37 5个回答非空", _p37_answers_non_empty),
    ("P38 回答引用正确宝宝名", _p38_answers_reference_correct_baby),
    ("P39 焦点切换正确", _p39_focus_switching_correct),
]


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    print("=== 终极实战 harness（从零建档版）：5 宝宝碎片化信息随机归档 + 交叉提问 ===")
    print(f"种子: {SEED} | 碎片信息: {len(SHATTERED_INFO)} 条 | 宝宝: {len(BABY_GROUND_TRUTHS)} 个")
    print(f"模式: 空库起步，agent 必须从零建档 | Mock Provider 只返回当前消息提到的单个属性")
    print()

    sim = loop.run_until_complete(run_simulation())

    failed = []
    for name, fn in CHECKS:
        try:
            fn(sim)
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {name}: 异常 {type(e).__name__}: {e}")
            failed.append(name)

    # 诊断报告（不计 PASS/FAIL）
    _report_diagnostics(sim)

    loop.close()

    print(f"\n=== Summary: {len(CHECKS) - len(failed)}/{len(CHECKS)} passed ===")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("RESULT: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
