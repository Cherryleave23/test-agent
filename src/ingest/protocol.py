"""知识采集统一接口（MOD-knowledge-ingest，G1/C3）。

统一协议：不同来源（PDF/图片表格/爬虫/Excel）归一为 KnowledgeRecord 再入库。
- `KnowledgeRecord`：归一后的统一结构；`structured` 持有结构化产品对象（奶粉/营养品），
  `product_category` 打企业产品结构标签（供 kb 过滤/溯源）。
- `IngestAdapter`（= `UnifiedKnowledgeSource` 别名）：每个采集源实现 `fetch() -> List[KnowledgeRecord]`。
- `SeedAdapter` / `TextAdapter`：既有真实适配器（onboarding 灌数据 / 纯文本）。
- 真实适配器（WebCrawler / MarkdownProduct / PDF / OCR）：见 `ingest/adapters.py`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Protocol, runtime_checkable

from kb.store import KnowledgeStore


@dataclass
class KnowledgeRecord:
    source_type: str                      # web | text | hq | milk | nutrition | pdf | image_table
    title: str
    content: str
    metadata: dict = field(default_factory=dict)
    lang: str = "zh"
    product_category: str = ""            # 企业产品结构挂钩标签（奶粉/营养品/...），供 kb 过滤
    structured: Optional[Any] = None      # 结构化产品对象（MilkProduct/NutritionProduct），非文本源时承载


@runtime_checkable
class UnifiedKnowledgeSource(Protocol):
    def fetch(self) -> List[KnowledgeRecord]:
        ...


# 规范命名（PRD MOD-knowledge-ingest §二 用 IngestAdapter）
IngestAdapter = UnifiedKnowledgeSource


class SeedAdapter:
    """灌入初始 HQ 知识 + B-end 产品（onboarding / 演示用）。"""

    def __init__(self, store: KnowledgeStore, enterprise_id: str):
        self.store = store
        self.ent = enterprise_id

    def seed_hq_knowledge(self) -> None:
        self.store.add_hq_knowledge(
            "婴幼儿奶粉段位怎么选",
            "0-6个月建议1段奶粉；6-12个月2段；1-3岁3段。选择时注意奶源、配方注册号与宝宝适应情况。",
        )
        self.store.add_hq_knowledge(
            "益生菌对婴幼儿的作用",
            "益生菌有助于维持肠道菌群平衡，改善消化不良与腹泻；选择时注意菌株与适用人群。",
        )

    def seed_products(self, milks: list, nutritions: list) -> None:
        for m in milks:
            self.store.add_milk(m)
        for n in nutritions:
            self.store.add_nutrition(n)


class TextAdapter:
    """纯文本来源适配（最小可用实现）。"""

    def __init__(self, path: str, title: str = ""):
        self.path = path
        self.title = title

    def fetch(self) -> List[KnowledgeRecord]:
        with open(self.path, "r", encoding="utf-8") as f:
            text = f.read()
        return [KnowledgeRecord(source_type="text", title=self.title or self.path, content=text)]


# 真实适配器（WebCrawler / MarkdownProduct / PDF / OCR）统一在 ingest/adapters.py 实现。
# PDF / 图片表格 适配器：仍按计划（crawl4ai + MinerU / PaddleOCR）接入，本 P1 不实现（见 PRD non-goals）。

