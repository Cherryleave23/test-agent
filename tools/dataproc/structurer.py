"""结构化抽取 + 商品实体解析（PRD P3，落在 standalone 工具，零 src.*）。

- 规则抽取（正则抓 段位/净含量/品牌/适用年龄/制造商/注册号）作主/兜底。
- LLM（工具自带 provider）抽其余字段为 MilkProduct 形状 JSON；解析失败 → 退规则 +
  标 parse_failed，绝不编造。
- 锚定原文：只填文本中确实出现的字段；未提及留空。
- 实体解析（resolve）：reg_number 优先；否则 (brand,name,stage) 元组键兜底；已知目录可命中。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

from .llms import ToolLLMProvider

# 结构化字段白名单（MilkProduct 形状子集）
FIELD_KEYS = ["brand", "name", "stage", "net_content", "age_range",
              "manufacturer", "reg_number"]


@dataclass
class StructuringResult:
    fields: dict
    provider_used: str = "rule-only"
    low_conf: bool = False
    parse_failed: bool = False


_SYSTEM = (
    "你是母婴商品资料结构化助手。从给定文本中抽取商品字段，仅输出 JSON，"
    "字段键限定为：brand, name, stage, net_content, age_range, manufacturer, reg_number。"
    "文本未提及的字段设为空字符串。严禁编造文本中没有的信息。"
)

_PROMPT_TMPL = "请从以下商品资料抽取结构化字段（JSON）：\n\n{text}\n\n只输出 JSON。"


def _rule_extract(text: str) -> dict:
    f: dict = {k: "" for k in FIELD_KEYS}
    m = re.search(r"(\d+)\s*段", text)
    if m:
        f["stage"] = f"{m.group(1)}段"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:g|ml|克|毫升|kg|L|升)", text, re.I)
    if m:
        f["net_content"] = m.group(0).strip()
    m = re.search(r"(?:适用年龄|适合)\s*[:：]?\s*([\d岁月龄~\-]+)", text)
    if m:
        f["age_range"] = m.group(1).strip()
    m = re.search(r"制造商\s*[:：]\s*(\S+)", text)
    if m:
        f["manufacturer"] = m.group(1).strip()
    m = re.search(r"国食注字\S+", text)
    if m:
        f["reg_number"] = m.group(0).strip()
    else:
        m = re.search(r"注册[号证]+\s*[:：]?\s*(\S+)", text)
        if m:
            f["reg_number"] = m.group(1).lstrip(":").strip()
    # 品牌：常见母婴品牌词表（弱规则，命中即填；未命中留空由 LLM/人工补）
    for b in ("伊利", "飞鹤", "君乐宝", "贝因美", "惠氏", "美赞臣", "爱他美", "a2", "雀巢"):
        if b in text:
            f["brand"] = b
            break
    return f


def _merge(rule: dict, llm: dict) -> dict:
    out = dict(rule)
    for k in FIELD_KEYS:
        v = (llm.get(k) or "").strip()
        if v:
            out[k] = v
    return out


def structure(text: str, provider: Optional[ToolLLMProvider] = None) -> StructuringResult:
    rule = _rule_extract(text)
    if not provider:
        # 无 LLM：纯规则兜底
        low = all(not rule[k] for k in ("stage", "net_content", "brand", "reg_number"))
        return StructuringResult(fields=rule, provider_used="rule-only", low_conf=low)

    try:
        raw = provider.complete(_PROMPT_TMPL.format(text=text[:4000]), system=_SYSTEM)
    except Exception:
        # LLM 不可用：退规则，标 low_conf（因本应走 LLM）
        return StructuringResult(fields=rule, provider_used="rule-only(fallback)",
                                 low_conf=True)
    parsed = _extract_json(raw)
    if parsed is None:
        return StructuringResult(fields=rule, provider_used=provider.label,
                                 low_conf=True, parse_failed=True)
    merged = _merge(rule, parsed)
    return StructuringResult(fields=merged, provider_used=provider.label, low_conf=False)


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
