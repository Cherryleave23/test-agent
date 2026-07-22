"""tools/dataproc 工具自管 schema（零 src.* 依赖，对应产物契约）。

- ProductRecord  → products.ndjson（结构化产品）
- CorpusRecord   → corpus.ndjson（非结构化 RAG 文本，含 kind 判别器）
- HQProductRecord→ hq_products.ndjson（HQ 商品库种子）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProductRecord:
    kind: str            # milk | nutrition
    uid: str             # reg:<号> | tuple:<sha1>
    status: str          # confirmed | pending
    source_ref: str      # 来源相对路径
    resolved: dict       # 实体解析结果
    fields: dict         # 结构化字段（不含 enterprise_id/id）

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "uid": self.uid, "status": self.status,
            "source_ref": self.source_ref, "resolved": self.resolved,
            "fields": self.fields,
        }


@dataclass
class CorpusRecord:
    part: str            # b_kb | hq_kb
    kind: str            # product_text | article | ingredient
    title: str
    content: str
    product_uid: Optional[str] = None
    meta: dict = field(default_factory=dict)
    lang: str = "zh"

    def to_dict(self) -> dict:
        d = {
            "part": self.part, "kind": self.kind, "title": self.title,
            "content": self.content, "lang": self.lang, "meta": self.meta,
        }
        if self.product_uid:
            d["product_uid"] = self.product_uid
        return d


@dataclass
class HQProductRecord:
    kind: str
    fields: dict
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "fields": self.fields, "meta": self.meta}
