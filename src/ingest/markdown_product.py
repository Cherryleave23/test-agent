"""Markdown 商品 → MilkProduct 适配器（B 端商品入库的「统一多源接口」一端）。

输入：母婴商品知识库导出的 markdown（YAML frontmatter + 正文表格/配料表/营养成分）。
这些文件含脏数据（trailing 全角逗号、"400g，g" 之类 artifacts、截断厂商名），本适配器
做字段清洗并映射到 MilkProduct 的 14 字段，使不同来源（爬虫/手工/ERP）都能汇入同一模型。

用法：
    from ingest.markdown_product import parse_md_product, ingest_markdown_products
    prods = [parse_md_product(p) for p in Path(dir).glob("*.md")]
    ids = ingest_markdown_products(store, [p.path for p in prods], "ent_b")
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from kb.models import MilkProduct


def _clean(s: str) -> str:
    """清洗单值：去空白、去 trailing 全角/半角逗号与顿号、去包裹引号。"""
    if s is None:
        return ""
    s = s.strip()
    # 去掉末尾的 "，" "、" "，" 与多余标点（脏数据常见于爬虫导出）
    s = re.sub(r"[，,、；;]+$", "", s)
    s = s.strip().strip('"').strip("'")
    return s


def _clean_list(s: str) -> List[str]:
    """清洗 flow-list 风格值：[a, b， c] -> ['a','b','c']。"""
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    out = []
    for part in re.split(r"[,，、]", s):
        c = _clean(part)
        if c:
            out.append(c)
    return out


def _parse_frontmatter(text: str) -> dict:
    """解析 --- 包裹的 YAML-like frontmatter（容忍 trailing 全角逗号等脏数据）。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not m:
        return {}
    body = m.group(1)
    data: dict = {}
    for line in body.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if val.startswith("[") or "[" in val and "]" in val:
            data[key] = _clean_list(val)
        else:
            data[key] = _clean(val)
    return data


def _sections(body: str) -> dict:
    """按 '## 标题' 切分正文为 {标题: 文本}。"""
    secs: dict = {}
    cur = None
    buf: List[str] = []
    for line in body.splitlines():
        mm = re.match(r"^##\s+(.+?)\s*$", line)
        if mm:
            if cur is not None:
                secs[cur] = "\n".join(buf).strip()
            cur = mm.group(1).strip()
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        secs[cur] = "\n".join(buf).strip()
    return secs


def _table_row(secs: dict, label: str) -> str:
    """从 '基本信息' markdown 表格里取某行的值（按 **标签** 匹配）。"""
    table = secs.get("基本信息", "")
    for line in table.splitlines():
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        # 去掉表头/分隔行
        if set("".join(cells)) <= set("-: "):
            continue
        if len(cells) >= 2 and label in cells[0].replace("*", ""):
            return _clean(cells[1])
    return ""


def _formulas(secs: dict) -> List[str]:
    """从 '优点 / 特色配方' 提取 [[原料信息/DHA|DHA]] 中的显示名，去重。"""
    out: List[str] = []
    for key in ("优点 / 特色配方", "优点/特色配方", "特色配方", "优点"):
        txt = secs.get(key, "")
        if not txt:
            continue
        for mm in re.finditer(r"\[\[[^\]|]*\|([^\]]+)\]\]", txt):
            name = _clean(mm.group(1))
            if name and name not in out:
                out.append(name)
    return out


def parse_md_product(path: str | Path) -> MilkProduct:
    """解析单个商品 markdown → MilkProduct。"""
    text = Path(path).read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)

    # 正文（去掉 frontmatter）
    body = re.sub(r"^---\s*\n.*?\n---\s*\n?", "", text, flags=re.DOTALL)
    secs = _sections(body)

    # 商品名：优先正文 H1 标题
    h1 = re.search(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
    name = _clean(h1.group(1)) if h1 else ""
    if not name:
        name = _clean(f"{fm.get('brand','')}{fm.get('series','')}{fm.get('stage','')}")

    brand = _clean(str(fm.get("brand", "")))
    stage = _clean(str(fm.get("stage", ""))) or "其他"
    age_range = _clean(str(fm.get("age", "")))
    try:
        price = float(str(fm.get("price", "0")).strip() or "0")
    except ValueError:
        price = 0.0
    origin = _clean(str(fm.get("origin", "")))
    milk_origin = _clean(str(fm.get("milk_source", ""))) or origin
    ptype = _clean(str(fm.get("category", ""))) or "牛奶粉"
    reg_number = _clean(str(fm.get("reg_number", "")))
    manufacturer = _clean(str(fm.get("manufacturer", "")))

    ingredients = _clean(secs.get("配料表", ""))
    nutrition = _clean(secs.get("营养成分", ""))

    # highlights：keywords + 适用人群 + 优势总结 + 特色配方
    keywords = fm.get("keywords", [])
    if isinstance(keywords, str):
        keywords = _clean_list(keywords)
    audience = _table_row(secs, "适用人群")
    advantage = _table_row(secs, "优势总结")
    formulas = _formulas(secs)
    spec = _clean(str(fm.get("spec", "")))
    hl_parts = [audience, advantage] + formulas + list(keywords)
    if spec:
        hl_parts.append(f"规格{spec}")
    highlights = " ".join(p for p in hl_parts if p)
    keyword_str = " ".join(p for p in keywords if p)

    return MilkProduct(
        name=name,
        brand=brand,
        stage=stage,
        age_range=age_range,
        price=price,
        origin=origin,
        milk_origin=milk_origin,
        ptype=ptype,
        reg_number=reg_number,
        manufacturer=manufacturer,
        ingredients=ingredients,
        nutrition=nutrition,
        highlights=highlights,
        keywords=keyword_str,
        enterprise_id="",
    )


def ingest_markdown_products(store, paths: List[str | Path], enterprise_id: str) -> List[int]:
    """把若干商品 markdown 解析为 MilkProduct 并入库（store.add_milk）。

    返回各商品在 products_milk 表的主键列表。
    """
    ids: List[int] = []
    for p in paths:
        prod = parse_md_product(p)
        prod.enterprise_id = enterprise_id
        ids.append(store.add_milk(prod))
    return ids
