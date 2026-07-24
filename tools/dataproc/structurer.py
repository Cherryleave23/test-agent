"""结构化抽取 + 商品实体解析（PRD P3，落在 standalone 工具，零 src.*）。

- 分类感知：按 category（大类名 / schema 名）选字段键集与权威字段；
  奶粉走 MILK 键集（reg_number 为权威），营养品走 NUTRITION 键集（health_license 为权威）。
- 规则抽取（正则）作主/兜底；LLM（工具自带 provider）抽其余字段；
  解析失败 → 退规则 + 标 parse_failed，绝不编造。
- 锚定原文：只填文本中确实出现的字段；未提及留空。
- 实体解析（resolve）：reg_number 优先；否则 (brand,name,stage) 元组键兜底；已知目录可命中。

字段结构由 `schema_conf.load_schemas()` 提供（内置默认 + conf.yaml 自定义类目），
抽取层据此动态选键，从而支持企业自定义产品数据结构（"同一接口"对营养品/自定义类目成立）。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .llms import ToolLLMProvider
from .schema_conf import (
    load_schemas, schema_keys, auth_fields_for, resolve_schema_name,
    is_nutrition, MILK_KEYS, NUTRITION_KEYS,
)

# 兼容旧调用（harness / 测试可能引用）
FIELD_KEYS = ["brand", "name", "stage", "net_content", "age_range",
              "manufacturer", "reg_number"]
_OCR_AUTH_FIELDS = ("reg_number", "net_content", "stage")

# 常见母婴品牌词表（弱规则，命中即填；未命中留空由 LLM/人工补）
_BRAND_WORDS = ("伊利", "飞鹤", "君乐宝", "贝因美", "惠氏", "美赞臣",
                "爱他美", "a2", "雀巢")

# 功效词表（营养品 efficacy 弱规则）
_EFFICACY_WORDS = ("增强免疫力", "免疫力", "骨骼", "补钙", "视力", "叶黄素",
                   "益智", "肠道", "益生菌", "补铁", "补锌", "多维")


@dataclass
class StructuringResult:
    fields: dict
    provider_used: str = "rule-only"
    low_conf: bool = False
    parse_failed: bool = False
    needs_review: bool = False  # 规则与 LLM 在权威/描述字段上冲突 → 需人工复核


_SYSTEM_TMPL = (
    "你是母婴商品资料结构化助手。从给定文本中抽取商品字段，仅输出 JSON，"
    "字段键限定为：{keys}。"
    "文本未提及的字段设为空字符串。严禁编造文本中没有的信息。"
)

_PROMPT_TMPL = "请从以下商品资料抽取结构化字段（JSON）：\n\n{text}\n\n只输出 JSON。"


# ---------------------------------------------------------------------------
# 规则抽取
# ---------------------------------------------------------------------------
def _dedicated_rule_extract(text: str) -> dict:
    """专用正则：覆盖内置牛奶粉/营养品字段。返回 {key: value} 仅命中项。"""
    f: dict = {}

    # —— 奶粉 & 营养品共用 ——
    m = re.search(r"(\d+)\s*段", text)
    if m:
        f["stage"] = f"{m.group(1)}段"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:g|ml|克|毫升|kg|L|升)", text, re.I)
    if m:
        f["net_content"] = m.group(0).strip()
    m = re.search(r"(?:适用年龄|适合|适宜年龄)\s*[:：]?\s*([\d岁月龄~\-]+)", text)
    if m:
        f["age_range"] = m.group(1).strip()
    m = re.search(r"制造商\s*[:：]\s*(\S+)", text)
    if m:
        f["manufacturer"] = m.group(1).strip()

    # —— 奶粉 ——
    m = re.search(r"国食注字\s*\S+", text)
    if m:
        f["reg_number"] = m.group(0).strip()
    else:
        m = re.search(r"注册[号证]+\s*[:：]?\s*(\S+)", text)
        if m:
            f["reg_number"] = m.group(1).lstrip(":").strip()

    # —— 营养品 ——
    m = re.search(r"(?:国食健字|卫食健字|食健备|国食健备)\s*[GJ]?\s*[\w\d]+", text)
    if m:
        f["health_license"] = m.group(0).strip()
    else:
        m = re.search(r"SC\d+", text)
        if m:
            f["health_license"] = m.group(0).strip()
    m = re.search(r"适宜人群\s*[:：]?\s*(.+?)(?=\n|；|;|保健功能|国食|每日|每次|每[粒片袋瓶支滴勺包]|制造商|$)", text, re.S)
    if m:
        f["audience"] = m.group(1).strip()
    m = re.search(r"保健功能\s*[:：]?\s*(.+?)(?=\n|；|;|国食|每日|每次|每[粒片袋瓶支滴勺包]|制造商|$)", text, re.S)
    if m:
        f["efficacy"] = m.group(1).strip()
    else:
        for w in _EFFICACY_WORDS:
            if w in text:
                f["efficacy"] = w
                break
    m = re.search(r"每[粒片袋瓶支滴勺包]\s*含?\s*([^\n；;，、]+?)(?=\n|；|;|，|、|每日|每次|食用|$)", text)
    if m:
        f["active_ingredients"] = m.group(1).strip()
    m = re.search(r"(?:每日|每次)\s*[1-9][^\n；;]*?[粒片袋瓶支滴勺包]", text)
    if m:
        f["dosage"] = m.group(0).strip()
    else:
        m = re.search(r"食用量\s*[:：]?\s*([^\n；;]+)", text)
        if m:
            f["dosage"] = m.group(1).strip()

    # —— 品牌（弱规则，两者共用）——
    for b in _BRAND_WORDS:
        if b in text:
            f["brand"] = b
            break

    return f


def _label_rule_extract(text: str, keys: List[str],
                        schemas: dict, category: Optional[str]) -> dict:
    """通用 label/alias 抽取：覆盖自定义字段（如 probiotic 的 strain/cfu）。

    对每个仍空、且在 schema 中有 label/aliases 的字段，匹配 `label[:：]值`。
    """
    out: dict = {}
    name = resolve_schema_name(category, schemas)
    fields_by_key: dict = {}
    if name and name in schemas:
        for fd in schemas[name].get("fields", []):
            fields_by_key[fd["key"]] = fd
    for k in keys:
        if k in out and out[k]:
            continue
        spec = fields_by_key.get(k)
        if not spec:
            continue
        labels = [spec.get("label", "")] + list(spec.get("aliases", []) or [])
        for lbl in labels:
            if not lbl:
                continue
            m = re.search(rf"{re.escape(lbl)}\s*[:：]\s*([^\n；;]+)", text)
            if m:
                out[k] = m.group(1).strip()
                break
    return out


def _rule_extract(text: str, category: Optional[str] = None,
                  schemas: Optional[dict] = None) -> dict:
    schemas = schemas if schemas is not None else load_schemas()
    keys = schema_keys(category, schemas)
    f: dict = {k: "" for k in keys}
    # 1) 专用正则（内置字段）
    for k, v in _dedicated_rule_extract(text).items():
        if k in f:
            f[k] = v
    # 2) 通用 label/alias 抽取（自定义字段，如 strain/cfu）
    for k, v in _label_rule_extract(text, keys, schemas, category).items():
        if k in f and not f[k]:
            f[k] = v
    return f


# ---------------------------------------------------------------------------
# 融合（OCR 锚定，范式②核心）
# ---------------------------------------------------------------------------
def _as_str(v) -> str:
    """LLM 返回的 JSON 字段可能是 int/float/None，统一转 str 并去空。"""
    if v is None:
        return ""
    return str(v).strip()


def _fuse(rule: dict, llm: dict, keys: List[str],
          auth: Tuple[str, ...]) -> Tuple[dict, list]:
    """OCR 锚定融合（范式②核心）。

    - 权威字段（由 auth 决定，奶粉=reg_number/net_content/stage，
      营养品=health_license/net_content/active_ingredients）：以规则(OCR)为准；
      规则空才用 LLM；双方非空且不同 → 冲突，保留规则值并记 conflicts。
    - 描述字段：优先 LLM（语义补全）；规则空才用规则；双方非空且不同 → 冲突，
      保留规则值（保守、不编造）并记 conflicts。
    返回 (fields, conflicts)。
    """
    out: dict = {}
    conflicts: list = []
    for k in keys:
        rv = _as_str(rule.get(k))
        lv = _as_str(llm.get(k))
        if rv and lv and rv != lv:
            conflicts.append(k)
            out[k] = rv  # 冲突：保守保留规则(OCR)锚定值，交由人工复核
        elif k in auth:
            out[k] = rv or lv  # 权威字段：规则优先
        else:
            out[k] = lv or rv  # 描述字段：LLM 优先补全
    return out, conflicts


def structure(text: str, provider: Optional[ToolLLMProvider] = None,
              category: Optional[str] = None,
              schemas: Optional[dict] = None) -> StructuringResult:
    schemas = schemas if schemas is not None else load_schemas()
    # 分类推断（默认按文本）
    if category is None:
        from .classifier import classify
        cls = classify(text)
        category = cls.get("product_category") or ""
    keys = schema_keys(category, schemas)
    auth = auth_fields_for(category, schemas)

    rule = _rule_extract(text, category, schemas)
    if not provider:
        # 无 LLM：纯规则兜底
        low = not (rule.get("name") or rule.get("brand"))
        return StructuringResult(fields=rule, provider_used="rule-only", low_conf=low)

    system = _SYSTEM_TMPL.format(keys=", ".join(keys))
    try:
        raw = provider.complete(_PROMPT_TMPL.format(text=text[:4000]), system=system)
    except Exception:
        # LLM 不可用：退规则，标 low_conf（因本应走 LLM）
        return StructuringResult(fields=rule, provider_used="rule-only(fallback)",
                                 low_conf=True)
    parsed = _extract_json(raw)
    if parsed is None:
        return StructuringResult(fields=rule, provider_used=provider.label,
                                 low_conf=True, parse_failed=True)
    merged, conflicts = _fuse(rule, parsed, keys, auth)
    return StructuringResult(fields=merged, provider_used=provider.label,
                             low_conf=False, needs_review=bool(conflicts))


def _extract_json(raw: str) -> Optional[dict]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    # 容忍 ```json ... ``` 围栏
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _sha1(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p.encode("utf-8"))
    return h.hexdigest()


def resolve(fields: dict, known: Optional[List[dict]] = None) -> dict:
    """实体解析：返回 (uid, status, resolved)。reg_number 优先；否则元组键兜底。"""
    reg = (fields.get("reg_number") or "").strip()
    if reg:
        uid = "reg:" + reg
        status = "confirmed"
        resolved = {"match": "reg_number", "reg_number": reg,
                    "key": ["brand", "name", "stage"]}
        if known:
            for k in known:
                if (k.get("reg_number") or k.get("fields", {}).get("reg_number")) == reg:
                    status = "confirmed"
                    break
        return {"uid": uid, "status": status, "resolved": resolved}

    brand = (fields.get("brand") or "").strip()
    name = (fields.get("name") or "").strip()
    stage = (fields.get("stage") or "").strip()
    if brand and name and stage:
        uid = "tuple:" + _sha1(brand, name, stage)
        return {"uid": uid, "status": "pending",
                "resolved": {"match": "tuple", "key": ["brand", "name", "stage"]}}
    uid = "tuple:" + _sha1(json.dumps(fields, ensure_ascii=False, sort_keys=True))
    return {"uid": uid, "status": "pending",
            "resolved": {"match": "tuple", "key": sorted(fields.keys())}}
