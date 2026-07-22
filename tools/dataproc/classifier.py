"""商品类型推断（PRD P3，落在 standalone 工具，零 src.*）。

从文本中推断 `ptype`（牛奶粉/羊奶粉/有机奶粉/…）和 `product_category`（配方粉/营养品/…），
为 F3 kind 路由和结构化入库提供分类依据。

规则层：
  - 关键词匹配推断 ptype（"羊奶" → 羊奶粉，"有机" → 有机奶粉，默认 → 牛奶粉）
  - conf.yaml 的 product_category 映射覆盖（企业可自定义类别词表）

设计原则（与 structurer 一致）：
  - 锚定原文：只返回文本中确实出现的分类信号
  - 不编造：无匹配信号返回空串，由调用方决定 fallback
"""
from __future__ import annotations

import os
import re
import logging
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

logger = logging.getLogger(__name__)


# ptype 推断规则：按优先级从高到低匹配
# (正则模式, ptype 值)  —— 第一个命中即返回
_PTYPE_RULES: list = [
    (r"羊奶|羊乳|goat", "羊奶粉"),
    (r"有机|organic", "有机奶粉"),
    (r"氨基酸|amino\s*acid", "氨基酸配方粉"),
    (r"深度水解|部分水解|水解|hydrolyz", "水解蛋白奶粉"),
    (r"早产|low\s*birth\s*weight", "早产儿配方粉"),
    (r"牛奶|牛乳|cow|milk", "牛奶粉"),
]

# product_category 默认推断：基于关键词
_CATEGORY_RULES: list = [
    (r"奶粉|配方粉|formula|stage|\d\s*段", "配方粉"),
    (r"DHA|益生菌|维生素|钙|铁|锌|nutrient|营养", "营养品"),
    (r"米粉|米糊|辅食|cereal", "辅食"),
    (r"纸尿裤|尿不湿|diaper", "日用品"),
]

# 模块级缓存：conf.yaml 只读一次（文件 mtime 变更时刷新）
_overrides_cache: dict = {}
_overrides_path: Optional[str] = None
_overrides_mtime: float = 0.0


def classify_ptype(text: str) -> str:
    """从文本推断奶粉类型（ptype）。

    返回 "牛奶粉" | "羊奶粉" | "有机奶粉" | "水解蛋白奶粉" | "氨基酸配方粉" |
          "早产儿配方粉" | "" （无匹配）
    """
    if not text:
        return ""
    for pattern, ptype in _PTYPE_RULES:
        if re.search(pattern, text, re.I):
            return ptype
    return ""


def classify_category(text: str) -> str:
    """从文本推断商品大类（product_category）。

    返回 "配方粉" | "营养品" | "辅食" | "日用品" | "" （无匹配）
    """
    if not text:
        return ""
    for pattern, cat in _CATEGORY_RULES:
        if re.search(pattern, text, re.I):
            return cat
    return ""


def load_category_overrides(conf_path: Optional[str] = None) -> dict:
    """从 conf.yaml 加载企业自定义 product_category 映射（带 mtime 缓存）。

    conf.yaml 格式示例：
        product_categories:
          牛奶粉: 配方粉
          羊奶粉: 配方粉
          DHA: 营养品

    返回 {ptype_or_keyword: category} 映射；缺文件返回 {}。
    缓存策略：文件 mtime 未变时返回缓存，变更时重新加载。
    """
    global _overrides_cache, _overrides_path, _overrides_mtime
    if not conf_path:
        here = os.path.dirname(os.path.abspath(__file__))
        conf_path = os.path.join(here, "conf.yaml")
    if not os.path.isfile(conf_path) or yaml is None:
        return {}
    # mtime 缓存：路径相同且 mtime 未变时返回缓存
    try:
        mtime = os.path.getmtime(conf_path)
    except OSError:
        return {}
    if conf_path == _overrides_path and mtime == _overrides_mtime:
        return _overrides_cache
    try:
        with open(conf_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _overrides_cache = data.get("product_categories", {}) or {}
        _overrides_path = conf_path
        _overrides_mtime = mtime
        return _overrides_cache
    except Exception as e:
        logger.warning("conf.yaml 加载失败: %s: %s", type(e).__name__, e)
        return {}


def classify(text: str, conf_path: Optional[str] = None) -> dict:
    """完整分类：返回 {ptype, product_category}。

    优先使用 conf.yaml 覆盖，缺覆盖时用规则推断。
    """
    overrides = load_category_overrides(conf_path)
    ptype = classify_ptype(text)
    # 覆盖逻辑：如果 conf.yaml 有该 ptype 的映射，用映射值
    category = ""
    if ptype and ptype in overrides:
        category = overrides[ptype]
    if not category:
        # 尝试关键词覆盖
        for kw, cat in overrides.items():
            if kw in text:
                category = cat
                break
    if not category:
        category = classify_category(text)
    return {"ptype": ptype, "product_category": category}
