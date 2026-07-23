"""知识采集真实适配器 + 归一管线（MOD-knowledge-ingest，P1 扩展）。

> 重要约束（项目架构红线）：**agent 端不包含任何爬虫**。agent 的 RAG / 商品库数据**只能**
> 来自数据处理工具（tools/dataproc）处理后的 NDJSON bundle，由 `ingest.importer.load_bundle`
> 加载。本模块的适配器只用于**人工/运营数据录入**（把本地 markdown / 纯文本等已授权资料
> 归一后写入 store），**绝不**在 agent 运行时自行联网抓取。
>
> 爬虫是**独立的初级数据获取工具**（位于 dataproc 侧 `tools/dataproc/crawler.py`，零 src.* 依赖），
> 其产出由**用户手动**放入 dataproc 的「产品知识」后，再经 dataproc 处理、打包、入包，最终被
> agent 消费——爬虫永远不是系统的内部数据源。

- `MarkdownProductAdapter`：包装既有 `parse_md_product`，把商品 markdown 纳为「统一接口」的一种实现，
  产出 `source_type="milk"` 的 `KnowledgeRecord`（`structured` 持有 `MilkProduct`）。
- `TextAdapter`：纯文本来源适配（运营/人工录入的已授权文本）。
- `IngestPipeline`：注册表 + 运行；按 `source_type` 路由到对应 sink；跨运行内容哈希去重；
  单适配器失败不中断整批、失败留痕（不静默丢弃，不谎称成功）。
- `PDFAdapter` / `ImageTableAdapter`：仍按计划（crawl4ai + MinerU / PaddleOCR）接入，本 P1 不实现。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List

from ingest.markdown_product import parse_md_product
from ingest.protocol import IngestAdapter, KnowledgeRecord
from kb.models import MilkProduct, NutritionProduct
from kb.store import KnowledgeStore


# ---------------------------------------------------------------------------
# 注册表（开闭原则：新增来源在 adapters.py 注册，不动 IngestPipeline 核心）
# ---------------------------------------------------------------------------
REGISTRY: dict = {}


def register(name: str, adapter):
    """把适配器实例登记进全局注册表（供 IngestPipeline.run_registered 调用）。"""
    REGISTRY[name] = adapter
    return adapter


# ---------------------------------------------------------------------------
# Markdown 商品适配器：把既有 parse_md_product 纳为统一接口的一种实现
# ---------------------------------------------------------------------------
class MarkdownProductAdapter:
    """商品 markdown → 统一 `KnowledgeRecord`（`source_type="milk"`，structured 持有 MilkProduct）。"""

    def __init__(self, paths):
        self.paths = [Path(p) for p in paths]

    def fetch(self) -> List[KnowledgeRecord]:
        recs: List[KnowledgeRecord] = []
        for p in self.paths:
            prod = parse_md_product(p)
            if not prod or not prod.name:
                continue
            recs.append(KnowledgeRecord(
                source_type="milk",
                title=prod.name,
                content=prod.to_search_text(),
                metadata={"brand": prod.brand, "stage": prod.stage,
                          "ptype": prod.ptype, "source": "markdown"},
                lang="zh",
                product_category="milk",
                structured=prod,
            ))
        return recs


# ---------------------------------------------------------------------------
# PDF / 图片表格 适配器：仍按计划接入，本 P1 不实现（见 PRD non-goals）
# ---------------------------------------------------------------------------
class PDFAdapter:
    def __init__(self, path: str):
        self.path = path

    def fetch(self) -> List[KnowledgeRecord]:
        raise NotImplementedError("PDF 解析适配器：计划 crawl4ai + MinerU，P1 未实现")


class ImageTableAdapter:
    def __init__(self, path: str):
        self.path = path

    def fetch(self) -> List[KnowledgeRecord]:
        raise NotImplementedError("图片/表格 OCR 适配器：计划 PaddleOCR，P1 未实现")


# ---------------------------------------------------------------------------
# 归一管线：注册表 + 运行 + 路由 + 去重 + 容错
# ---------------------------------------------------------------------------
class IngestPipeline:
    """把各异构适配器产出的 `KnowledgeRecord` 归一写入知识库。

    路由规则（按 source_type）：
      - `milk`    → store.add_milk(structured)
      - `nutrition` → store.add_nutrition(structured)
      - `hq`      → store.add_hq_knowledge（HQ 共享库）
      - `text`/`pdf`/`image_table` → store.add_knowledge（企业自有 RAG 知识，人工/运营录入）
      - `web`：本 agent 不自行产出；仅当外部（dataproc 爬虫工具产出、经处理后）显式导入时落入 RAG 知识。

    去重：内容哈希（source_type + content/title）持久化到 `ingest_dedup` 表，跨运行不重复入库。
    容错：单个适配器 fetch 抛错时记录到 `failures` 并返回 0，**不中断整批**、不静默丢弃。
    """

    def __init__(self, store: KnowledgeStore, enterprise_id: str, dedup: bool = True):
        self.store = store
        self.ent = enterprise_id
        self.dedup = dedup
        self.failures: List[dict] = []

    @staticmethod
    def _chash(rec: KnowledgeRecord) -> str:
        key = f"{rec.source_type}|{rec.content or rec.title}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def run(self, adapter: IngestAdapter, name: str = "adapter") -> int:
        """运行一个适配器，返回成功入库的记录数。异常被捕获并记录到 failures。"""
        try:
            records = adapter.fetch()
        except Exception as e:  # noqa: BLE001
            self.failures.append({"source": name, "error": repr(e)})
            return 0
        n = 0
        for rec in records:
            if self._ingest_one(rec, name):
                n += 1
        return n

    def run_registered(self, name: str) -> int:
        if name not in REGISTRY:
            raise KeyError(f"未注册适配器: {name}")
        return self.run(REGISTRY[name], name)

    def run_all(self) -> dict:
        """运行注册表中全部适配器，返回 {name: 入库数}。"""
        out = {}
        for name in REGISTRY:
            out[name] = self.run(REGISTRY[name], name)
        return out

    def _ingest_one(self, rec: KnowledgeRecord, name: str) -> bool:
        chash = self._chash(rec)
        if self.dedup and self.store.is_ingested(self.ent, rec.source_type, chash):
            return False
        ok = self._route(rec)
        if ok and self.dedup:
            self.store.mark_ingested(self.ent, rec.source_type, chash)
        return ok

    def _route(self, rec: KnowledgeRecord) -> bool:
        st = rec.source_type
        if st == "milk" and isinstance(rec.structured, MilkProduct):
            rec.structured.enterprise_id = self.ent
            self.store.add_milk(rec.structured)
            return True
        if st == "nutrition" and isinstance(rec.structured, NutritionProduct):
            rec.structured.enterprise_id = self.ent
            self.store.add_nutrition(rec.structured)
            return True
        if st == "hq":
            self.store.add_hq_knowledge(rec.title, rec.content)
            return True
        # web / text / pdf / image_table -> 企业自有 RAG 知识
        self.store.add_knowledge(self.ent, rec.title, rec.content, rec.metadata or {})
        return True
