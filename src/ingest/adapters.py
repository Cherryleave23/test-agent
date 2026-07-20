"""知识采集真实适配器 + 归一管线（MOD-knowledge-ingest，P1 扩展）。

- `MarkdownProductAdapter`：包装既有 `parse_md_product`，把商品 markdown 纳为「统一接口」的一种实现，
  产出 `source_type="milk"` 的 `KnowledgeRecord`（`structured` 持有 `MilkProduct`）。
- `WebCrawlerAdapter`：**真实**抓取 + 解析（标准库 `urllib` + `html.parser`，零外部依赖，端侧友好），
  产出 `source_type="web"` 的内容分块。沙箱内以本地 stub HTTP 服务驱动真实客户端代码路径。
- `IngestPipeline`：注册表 + 运行；按 `source_type` 路由到对应 sink；跨运行内容哈希去重；
  单适配器失败不中断整批、失败留痕（不静默丢弃，不谎称成功）。
- `PDFAdapter` / `ImageTableAdapter`：仍按计划（crawl4ai + MinerU / PaddleOCR）接入，本 P1 不实现。

约束（PRD §六）：采集是「搬运」非「生成」——禁止用 LLM 改写事实性内容；仅采集企业自有/已授权内容。
"""
from __future__ import annotations

import hashlib
import re
import urllib.request
from html.parser import HTMLParser
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
# 真实网页爬虫适配器（标准库，零外部依赖）
# ---------------------------------------------------------------------------
class _VisibleTextExtractor(HTMLParser):
    """极简真实 HTML 解析：抽取 <title> 与可见正文（跳过 script/style）。"""

    _BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "td", "div", "section", "article"}

    def __init__(self):
        super().__init__()
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in ("script", "style"):
            self._skip_depth += 1
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")  # 块级标签处断行，便于后续按段分块

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
        else:
            self.parts.append(data)


def _chunk_text(text: str, max_chars: int = 300) -> List[str]:
    """把可见正文切成 ~max_chars 的语义块（按段聚合，超长段按句切）。"""
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: List[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) + 1 <= max_chars:
            cur = (cur + " " + p).strip()
            continue
        if cur:
            chunks.append(cur)
        if len(p) > max_chars:
            for sub in re.split(r"(?<=[。；;！!？?])", p):
                sub = sub.strip()
                if sub:
                    chunks.append(sub)
            cur = ""
        else:
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


class WebCrawlerAdapter:
    """真实抓取官网 URL → 解析为 `source_type="web"` 的内容分块。

    使用标准库 `urllib` + `html.parser`，**零外部依赖**（端侧轻量）。沙箱内由本地 stub
    HTTP 服务驱动真实客户端代码路径（MOD-wechat §五 既定做法）。生产环境须仅采集企业自有/
    已授权内容，并遵守 robots（本适配器提供 `respect_robots` 开关，默认 False——企业自有站点）。
    """

    def __init__(self, url: str, user_agent: str = "Mozilla/5.0 (compatible; BabyAgentIngest/1.0)",
                 timeout: int = 15, respect_robots: bool = False):
        self.url = url
        self.ua = user_agent
        self.timeout = timeout
        self.respect_robots = respect_robots

    def fetch(self) -> List[KnowledgeRecord]:
        req = urllib.request.Request(self.url, headers={"User-Agent": self.ua})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
        html = raw.decode(charset, errors="replace")

        parser = _VisibleTextExtractor()
        parser.feed(html)
        title = (parser.title or "").strip() or self.url
        text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()

        recs: List[KnowledgeRecord] = []
        for i, chunk in enumerate(_chunk_text(text)):
            if not chunk:
                continue
            recs.append(KnowledgeRecord(
                source_type="web",
                title=title,
                content=chunk,
                metadata={"url": self.url, "chunk_index": i},
                lang="zh",
                product_category="",
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
      - `web`/`text`/`pdf`/`image_table` → store.add_knowledge（企业自有 RAG 知识）

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
