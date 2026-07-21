"""意图消歧 + 属性抽取（MOD-baby-profile，P2 阶段2）。

`resolve_and_extract` 每轮把「近期上下文 + 当前消息 + 该员工已知 客户→宝宝 清单 + 本会话焦点宝宝」
喂给 LLM，输出：当前在聊哪个宝宝（实体链接，处理快速切换/代词指代）、抽取到哪些宝宝属性、
以及这是否关于真实管理的宝宝（第三人称/假设则不建档）。

兜底：LLM 输出解析失败 → 退化为规则抽取（`session.constraints.extract_constraints`）+ 沿用本会话焦点。
成本优化：当前消息无宝宝相关信号时短路，跳过 LLM 调用。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

from session.constraints import extract_constraints
from baby.models import BabyProfile


@dataclass
class ResolutionResult:
    """LLM 消歧/抽取的结构化结果。"""

    action: str = "chat"          # chat | new_customer | new_baby | confirm | merge | delete
    customer: str = ""            # 客户名/标识（用于定位或新建）
    baby: str = ""                # 宝宝名/标识
    customer_id: Optional[int] = None
    baby_id: Optional[int] = None
    extracted: BabyProfile = None  # 抽取的宝宝属性（ent/emp/cid 由调用方补全）
    is_third_party: bool = False   # 聊的是别人/同事的宝宝（不建档）
    is_hypothetical: bool = False  # 假设/举例（不建档）
    parse_failed: bool = False     # LLM 输出解析失败（兜底退化，供熔断统计）
    raw: str = ""


# 稳定前缀（Prompt Caching 缓存对象）：纯指令，不含任何每轮变量。
# 生产部署时可在其后追加「企业定制产品结构/消歧规则」，进一步放大缓存收益。
_SYSTEM_INSTRUCTION = """你是母婴导购助手的「宝宝意图消歧器」。根据对话上下文，判断员工当前在聊哪个宝宝，并抽取宝宝属性。

（该员工的客户与宝宝清单、本会话焦点宝宝 id 将在下方以结构化形式提供；请勿在回复中复述它们。）

只输出一个 JSON 对象，不要解释、不要代码块标记。字段：
- action: 字符串。chat=在聊已建档案的宝宝；new_baby=聊到一个全新且能识别身份的宝宝（需建档）；new_customer=连客户都是新的；confirm=员工在确认/认可刚才建的档案；merge=要把某宝宝合并到另一个；delete=要删除某宝宝档案。
- customer: 客户名/称呼（如「张姐」），没有则空串。
- baby: 宝宝名/昵称（如「壮壮」），没有则空串。
- extracted: 宝宝属性对象，字段 baby_age(字符串), gender(字符串), stage(字符串), allergens(字符串数组), budget(数字或null), brand_preference(字符串数组), category(字符串), health_notes(字符串), birth_date(字符串,ISO格式如"2025-05-21"), gestational_weeks(整数或null,孕周), medical_history(字符串数组,医疗史如"早产35周""出生5.18斤""新生儿科9天"), feeding_history(字符串数组,喂养史如"混合喂养→纯奶粉""非喷射性吐奶至4-5个月已缓解"). 只填对话中明确提到的，没有则空。
- is_third_party: 布尔。若聊的是「同事的/别人的/网上的」宝宝而非本员工管理的客户宝宝，则 true（不要建档）。
- is_hypothetical: 布尔。若只是假设/举例/「如果…」，则 true（不要建档）。

注意：员工可能在一句话里从 A 客户宝宝切到 B 客户宝宝再切回 A，请严格依据上下文判定当前这句指向谁。"""

# 半稳定层头部：已知清单（同员工会话内大部分时间不变，仅新增宝宝时变化）
_KNOWN_HEADER = "\n\n已知该员工的客户与宝宝清单（JSON 数组）：\n"

# 无宝宝相关信号时的短路启发式（避免每轮都调 LLM）
_BABY_SIGNALS = re.compile(
    r"宝宝|宝贝|娃|婴儿|幼儿|月龄|个月|段位|段奶|奶粉|过敏|客户|顾客|张姐|李姐|"
    r"他家|她家|咱们|咱家|你家的|我家|小朋友|小孩",
    re.IGNORECASE,
)


def _has_baby_signal(text: str, known: Optional[List[dict]] = None) -> bool:
    """是否有「与宝宝/客户相关」的信号。

    命中固定关键词（如 奶粉/过敏/段位），或提到已建档的宝宝名/客户名，
    都算有信号——提到已知名字时必须走 LLM 消歧，不能短路沿用焦点。
    """
    if _BABY_SIGNALS.search(text or ""):
        return True
    if known:
        for it in known:
            bn = it.get("baby_name")
            cn = it.get("customer_name")
            if (bn and bn in (text or "")) or (cn and cn in (text or "")):
                return True
    return False


# 第三方/假设提及：焦点稳定缓存路径需让位给 LLM 判定（安全网不可绕过）
_THIRD_PARTY_HINTS = re.compile(r"同事|别人|他人|网上|网红|朋友家|隔壁", re.IGNORECASE)


def focus_is_stable(known: List[dict], focus_baby_id: Optional[int],
                    current_msg: str) -> bool:
    """结果缓存判据：焦点是否稳定到可跳过 LLM 实体链接。

    返回 True 当且仅当：已有焦点宝宝；且当前消息未提及任何「非焦点」的已知宝宝/客户名
    （否则可能是快速切换）；且不含第三方/假设提及（应交 LLM 判定 is_third_party）；
    且消息含宝宝信号时必须提及焦点宝宝/客户名（否则可能是新宝宝，交 LLM 建档）。

    稳定时调用方可用规则抽取直接归档到焦点宝宝，省去一次 LLM 调用——
    属性抽取本就是规则（LLM 仅做实体链接），质量无损。
    """
    if focus_baby_id is None:
        return False
    if _THIRD_PARTY_HINTS.search(current_msg or ""):
        return False
    focus_names = set()
    for it in known:
        if it.get("baby_id") == focus_baby_id:
            fn = _norm(it.get("baby_name", ""))
            cn = _norm(it.get("customer_name", ""))
            if fn:
                focus_names.add(fn)
            if cn:
                focus_names.add(cn)
    msg_n = _norm(current_msg)
    # 检测 known 中非焦点宝宝名（快速切换检测）
    for it in known:
        nb = _norm(it.get("baby_name", ""))
        nc = _norm(it.get("customer_name", ""))
        if nb and nb not in focus_names and nb in msg_n:
            return False
        if nc and nc not in focus_names and nc in msg_n:
            return False
    # 新增：消息含宝宝信号但不含焦点宝宝名/客户名 → 可能是新宝宝，交 LLM 建档
    # （缺陷修复：原逻辑只检测 known 中已有的非焦点名，不检测新宝宝名，
    #   导致从零建档场景下新宝宝信息被错误归到焦点宝宝）
    if _BABY_SIGNALS.search(current_msg or ""):
        if not any(fn in msg_n for fn in focus_names):
            # D4 修复：消息含代词指代信号且不含任何已知宝宝名 → 代词指代焦点，规则短路
            # （降低 LLM 调用率：代词指代焦点时无需 LLM 实体链接）
            if _PRONOUN_SIGNALS.search(current_msg or ""):
                # 再次确认不含 known 中其他宝宝名（快速切换检测已在上方完成）
                return True
            return False  # 无代词 → 可能新宝宝，交 LLM
    return True


# 结构化字段规则抽取词表（D1 修复：_rule_extract 支持 v2 字段）
_BIRTH_DATE_RE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?")
_GESTATIONAL_WEEKS_RE = re.compile(r"早产\s*(\d{1,2})\s*周|孕周\s*(\d{1,2})|(\d{1,2})\s*周出生")
_MEDICAL_HISTORY_KEYWORDS = [
    ("NICU", "NICU住院"), ("新生儿科", "新生儿科"), ("脑室出血", "脑室出血"),
    ("贫血", "贫血"), ("早产", None),  # "早产"单独处理（与 gestational_weeks 联动）
    ("黄疸", "黄疸"), ("低体重", "低体重"), ("窒息", "窒息"),
    ("先心", "先心病"), ("心脏", "心脏问题"),
]
_FEEDING_HISTORY_KEYWORDS = [
    ("混合喂养", "混合喂养"), ("纯奶粉", "纯奶粉"), ("纯母乳", "纯母乳"),
    ("水解奶粉", "深度水解奶粉"), ("氨基酸奶粉", "氨基酸奶粉"),
    ("吐奶", "吐奶"), ("喷射性吐奶", "喷射性吐奶"),
    ("辅食", None),  # 辅食相关不作为喂养史
]
# 代词指代信号（D4 修复：focus_is_stable 代词优化）
_PRONOUN_SIGNALS = re.compile(r"他|她|它|这个|这宝宝|这个宝宝|咱|咱们家|咱家|娃", re.IGNORECASE)


def _extract_birth_date(text: str) -> str:
    """从文本中抽取 ISO 格式出生日期（YYYY-MM-DD）。"""
    m = _BIRTH_DATE_RE.search(text or "")
    if m:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        return f"{y}-{mo:02d}-{d:02d}"
    return ""


def _extract_gestational_weeks(text: str) -> Optional[int]:
    """从文本中抽取孕周数（如 '早产35周' → 35）。"""
    m = _GESTATIONAL_WEEKS_RE.search(text or "")
    if m:
        for g in m.groups():
            if g:
                try:
                    v = int(g)
                    if 20 <= v <= 45:  # 合理孕周范围
                        return v
                except ValueError:
                    pass
    return None


def _extract_medical_history(text: str) -> List[str]:
    """从文本中抽取医疗史关键词。"""
    result = []
    t = text or ""
    for kw, label in _MEDICAL_HISTORY_KEYWORDS:
        if kw in t:
            if label is None:
                # "早产"单独处理：构造完整描述
                gw = _extract_gestational_weeks(t)
                if gw:
                    result.append(f"早产{gw}周")
                else:
                    result.append("早产")
            elif label not in result:
                result.append(label)
    # 出生体重（如 "5.18斤" "出生5斤"）
    m = re.search(r"出生[^\d]*?(\d+\.?\d*)\s*斤", t)
    if m:
        result.append(f"出生{m.group(1)}斤")
    # 住院天数（如 "NICU住院15天" "新生儿科住了9天"）
    m = re.search(r"(?:NICU|新生儿科)[^\d]*?住院?\s*(\d+)\s*天|住了\s*(\d+)\s*天", t)
    if m:
        days = m.group(1) or m.group(2)
        location = "NICU" if "NICU" in t else "新生儿科"
        result.append(f"{location}住院{days}天")
    return result


def _extract_feeding_history(text: str) -> List[str]:
    """从文本中抽取喂养史关键词。"""
    result = []
    t = text or ""
    for kw, label in _FEEDING_HISTORY_KEYWORDS:
        if kw in t and label and label not in result:
            result.append(label)
    # 奶粉转换序列（如 "深度水解后来氨基酸现在a2至初"）
    m = re.search(r"(深度水解|水解).{0,10}?(氨基酸).{0,10}?([aA]\d|a2|爱他美|飞鹤|合生元)", t)
    if m and "深度水解奶粉→氨基酸奶粉→a2至初" not in result:
        # 尝试构造转换序列
        parts = []
        if "深度水解" in t or "水解" in t:
            parts.append("深度水解奶粉")
        if "氨基酸" in t:
            parts.append("氨基酸奶粉")
        # 检查是否有具体品牌名作为终点
        for brand in ["a2至初", "a2", "爱他美", "飞鹤", "合生元"]:
            if brand in t:
                parts.append(brand)
                break
        if len(parts) >= 2:
            result.append("→".join(parts))
    return result


def _rule_extract(text: str) -> BabyProfile:
    """规则抽取（复用 UserConstraints 词表 + v2 结构化字段），返回仅含属性的 BabyProfile 壳。

    D1 修复：原 _rule_extract 只抽取基础字段（baby_age/stage/allergens/budget/brand/category），
    不抽取 v2 结构化字段（birth_date/gestational_weeks/medical_history/feeding_history），
    导致规则短路路径丢失这些字段。
    """
    c = extract_constraints(text or "")
    birth_date = _extract_birth_date(text)
    gestational_weeks = _extract_gestational_weeks(text)
    medical_history = _extract_medical_history(text)
    feeding_history = _extract_feeding_history(text)
    return BabyProfile(
        baby_id=None, enterprise_id="", employee_id="", customer_id=0,
        name="", baby_age=c.baby_age, stage=c.stage,
        allergens=list(c.allergens), budget=c.budget,
        brand_preference=list(c.brand_preference), category=c.category,
        health_notes=c.notes,
        birth_date=birth_date,
        gestational_weeks=gestational_weeks,
        medical_history=medical_history,
        feeding_history=feeding_history,
    )


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip()).lower()


def _match_known(known: List[dict], customer: str, baby: str):
    """按 客户名/宝宝名 在已知清单中定位，返回 (customer_id, baby_id) 或 (None, None)。

    防跨客户误配（缺陷 B）：给了客户名时**只**做 (客户,宝宝) 精确匹配，绝不跨客户按宝宝名兜底；
    仅给宝宝名且全局唯一才匹配；同名多客户视为歧义返回 None（不自动匹配，交回焦点/显式建档）。

    D2 修复：精确 (客户,宝宝) 匹配失败时，若宝宝名全局唯一则退化为按宝宝名单独匹配——
    客户名可能尚未更新（建档时为「（未命名客户）」），不应因此导致焦点兜底串档。
    """
    nb, nc = _norm(baby), _norm(customer)
    if nb and nc:
        for it in known:
            if (_norm(it.get("baby_name", "")) == nb
                    and _norm(it.get("customer_name", "")) == nc):
                return it.get("customer_id"), it.get("baby_id")
        # D2 修复：精确匹配失败 → 检查是否有同名宝宝且客户名为「（未命名客户）」
        # 客户名尚未更新时（建档时未提供客户名）→ 匹配（D2 场景）
        # 客户名已是真实客户名 → 跨客户新宝宝，不匹配（P10 防污染场景）
        hits = [it for it in known if _norm(it.get("baby_name", "")) == nb]
        if len(hits) == 1:
            cust_name = hits[0].get("customer_name", "")
            if cust_name == "（未命名客户）":
                return hits[0].get("customer_id"), hits[0].get("baby_id")
        return None, None  # 跨客户或同名歧义 → 不自动匹配
    if nb:
        hits = [it for it in known if _norm(it.get("baby_name", "")) == nb]
        if len(hits) == 1:
            return hits[0].get("customer_id"), hits[0].get("baby_id")
        return None, None  # 同名多客户 → 歧义，不自动匹配
    if nc:
        matches = [it for it in known if _norm(it.get("customer_name", "")) == nc]
        if len(matches) == 1:
            return matches[0].get("customer_id"), matches[0].get("baby_id")
        if len(matches) > 1 and baby == "":
            # 同一客户多个宝宝且无宝宝名 -> 歧义，交回焦点
            return matches[0].get("customer_id"), None
    return None, None


def _parse_resolution(raw: str, known: List[dict], focus_baby_id: Optional[int]) -> ResolutionResult:
    """解析 LLM 输出为 ResolutionResult；失败兜底退化为规则抽取 + 沿用焦点。"""
    if not raw:
        return ResolutionResult(action="chat", baby_id=focus_baby_id,
                                extracted=_rule_extract(""), parse_failed=True)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return ResolutionResult(action="chat", baby_id=focus_baby_id,
                                extracted=_rule_extract(raw), parse_failed=True)
    try:
        d = json.loads(m.group(0))
    except (json.JSONDecodeError, TypeError):
        return ResolutionResult(action="chat", baby_id=focus_baby_id,
                                extracted=_rule_extract(raw), parse_failed=True)

    customer = d.get("customer") or ""
    baby = d.get("baby") or ""
    cid, bid = _match_known(known, customer, baby)
    if bid is None and d.get("action") in ("chat", "confirm", "merge", "delete"):
        # 聊已建档宝宝但没匹配到 -> 用焦点兜底
        bid = focus_baby_id
    ext_d = d.get("extracted") or {}
    gw = ext_d.get("gestational_weeks")
    try:
        gw = int(gw) if gw is not None else None
    except (TypeError, ValueError):
        gw = None
    extracted = BabyProfile(
        baby_id=None, enterprise_id="", employee_id="", customer_id=cid or 0,
        name=baby,
        baby_age=ext_d.get("baby_age") or "",
        gender=ext_d.get("gender") or "",
        stage=ext_d.get("stage") or "",
        allergens=list(ext_d.get("allergens") or []),
        budget=ext_d.get("budget"),
        brand_preference=list(ext_d.get("brand_preference") or []),
        category=ext_d.get("category") or "",
        health_notes=ext_d.get("health_notes") or "",
        birth_date=ext_d.get("birth_date") or "",
        gestational_weeks=gw,
        medical_history=list(ext_d.get("medical_history") or []),
        feeding_history=list(ext_d.get("feeding_history") or []),
    )
    return ResolutionResult(
        action=d.get("action", "chat"),
        customer=customer, baby=baby,
        customer_id=cid, baby_id=bid,
        extracted=extracted,
        is_third_party=bool(d.get("is_third_party", False)),
        is_hypothetical=bool(d.get("is_hypothetical", False)),
        raw=raw,
    )


async def resolve_and_extract(
    history_text: str,
    current_msg: str,
    known: List[dict],
    focus_baby_id: Optional[int],
    provider,
) -> ResolutionResult:
    """每轮消歧 + 抽取。

    - 无宝宝信号 → 短路，沿用焦点，规则抽取（省一次 LLM）。
    - 否则一次 LLM 调用，解析为 ResolutionResult；失败兜底。

    Prompt Caching：稳定前缀（指令 + 已知清单 `known`）作为首条 system 消息，
    每轮变量（`focus` + 历史 + 当前句）置于其后并开启 `cache_control`——
    同员工会话中前缀高度稳定（仅新增宝宝时变化），provider 复用前缀可显著降低
    input token 费用；切换焦点宝宝不破坏缓存（焦点在断点之后）。
    """
    if not _has_baby_signal(current_msg, known):
        return ResolutionResult(
            action="chat", baby_id=focus_baby_id,
            extracted=_rule_extract(current_msg),
        )
    known_json = json.dumps(known, ensure_ascii=False)
    # 稳定前缀：指令 + 已知清单（同员工跨轮一致，缓存命中率高）
    stable_prefix = _SYSTEM_INSTRUCTION + _KNOWN_HEADER + known_json
    # 每轮变量：焦点 + 历史 + 当前句（在缓存断点之后，不破坏前缀稳定性）
    user_turn = (
        f"本会话当前焦点宝宝 id：{focus_baby_id}"
        f"（若为 null 表示尚无焦点；用「他/她/宝宝/这个」等代词时默认指它）。\n\n"
        f"{history_text}\nuser: {current_msg}"
    )
    messages = [
        {"role": "system", "content": stable_prefix},
        {"role": "user", "content": user_turn},
    ]
    raw = await provider.complete(messages, cache_control=True)
    return _parse_resolution(raw, known, focus_baby_id)
