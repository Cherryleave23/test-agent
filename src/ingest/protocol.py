"""知识采集统一接口（MOD-knowledge-ingest，G1/C3）。

统一协议：不同来源（PDF/图片表格/爬虫/Excel）归一为 KnowledgeRecord 再入库。
MVP 实现 SeedAdapter（灌示例数据）与 TextAdapter；PDF/OCR/爬虫适配器保留接口。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol, runtime_checkable

from kb.store import KnowledgeStore


@dataclass
class KnowledgeRecord:
    source_type: str          # pdf | image_table | web | excel | seed
    title: str
    content: str
    metadata: dict = None
    lang: str = "zh"


@runtime_checkable
class UnifiedKnowledgeSource(Protocol):
    def fetch(self) -> List[KnowledgeRecord]:
        ...


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


# PDF / 图片表格 / 爬虫 适配器：MVP 保留接口契约，后续按 C3 接入 crawl4ai + PaddleOCR/MinerU
class PDFAdapter:
    def __init__(self, path: str):
        self.path = path

    def fetch(self) -> List[KnowledgeRecord]:
        raise NotImplementedError("PDF 解析适配器：MVP 未接入（计划 crawl4ai + MinerU）")


class ImageTableAdapter:
    def __init__(self, path: str):
        self.path = path

    def fetch(self) -> List[KnowledgeRecord]:
        raise NotImplementedError("图片/表格 OCR 适配器：MVP 未接入（计划 PaddleOCR）")


class WebCrawlerAdapter:
    def __init__(self, url: str):
        self.url = url

    def fetch(self) -> List[KnowledgeRecord]:
        raise NotImplementedError("官网爬虫适配器：MVP 未接入（计划 crawl4ai）")
