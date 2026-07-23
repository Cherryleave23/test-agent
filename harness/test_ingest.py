#!/usr/bin/env python3
# @module ingest
"""知识采集层（MOD-knowledge-ingest）真实运行验收 harness。

> 架构红线：agent 端**不含爬虫**。本 harness 只验证「人工/运营录入来源」（markdown 商品、
> 纯文本）经 `IngestPipeline` 归一写入 KnowledgeStore 的管线路由/去重/容错/集成。
> agent 的 RAG/商品库数据来自数据处理工具（tools/dataproc）处理后的 bundle（见
> `ingest.importer.load_bundle`）；爬虫是独立获取工具，不在此管线内。

断言（真实运行判 PASS/FAIL，非自述）：
  I2 markdown 适配器：产出 `source_type=milk` 且 `structured` 持有 MilkProduct。
  I3 统一接口：markdown / text 多源归一为同一 KnowledgeRecord 结构（同字段、同类型）。
  I4 去重：同内容二次入库计数为 0（跨运行内容哈希去重，ingest_dedup 表）。
  I5 容错：单适配器 fetch 抛错不中断整批、失败留痕、兄弟适配器仍入库。
  I6 集成：markdown 走管线入真实 store，产品落 products_milk 且可被 retrieve 命中（桥接 MOD-kb）。

直接运行：python3 test_ingest.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ingest.adapters import (  # noqa: E402
    IngestPipeline,
    MarkdownProductAdapter,
)
from ingest.protocol import KnowledgeRecord, TextAdapter  # noqa: E402
from kb.models import MilkProduct  # noqa: E402
from kb.store import KnowledgeStore  # noqa: E402


SAMPLE_MD = """---
brand: 贝贝优
series: 睿护
stage: 1段
age: 0-6个月
price: 368
origin: 中国
milk_source: 新西兰
category: 牛奶粉
reg_number: 国食注字YP20180012
manufacturer: 贝贝优营养品有限公司
spec: 400g
keywords: [益智, 易消化]
---

# 睿护婴儿配方奶粉1段

## 基本信息
| 项目 | 内容 |
| --- | --- |
| **适用人群** | 0-6个月婴儿 |
| **优势总结** | 含OPO结构脂 |

## 配料表
生牛乳、脱盐乳清粉、乳糖。

## 营养成分
蛋白质12.5g/100g，脂肪28g/100g，DHA 0.3%。

## 优点 / 特色配方
- DHA
- OPO结构脂
"""

SAMPLE_TXT = (
    "贝贝优 睿护婴儿配方奶粉1段，适合0-6个月宝宝。含OPO结构脂与益生菌Bb-12。"
    "奶源来自新西兰，国食注字YP20180012，厂商贝贝优营养品有限公司。"
)


def _tmp_md():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "ruihubaobao.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(SAMPLE_MD)
    return p


def _tmp_txt():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "ruihu.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write(SAMPLE_TXT)
    return p


# ---------------------------------------------------------------------------
# I2 markdown 适配器
# ---------------------------------------------------------------------------
def _i2_markdown():
    md = _tmp_md()
    recs = MarkdownProductAdapter([md]).fetch()
    assert recs, "markdown 适配器应产出非空记录"
    milk = [r for r in recs if r.source_type == "milk"]
    assert milk, "应存在 source_type=milk 的记录"
    r = milk[0]
    assert isinstance(r.structured, MilkProduct), "structured 应持有 MilkProduct"
    assert r.structured.name == "睿护婴儿配方奶粉1段", f"商品名解析错误: {r.structured.name}"
    assert r.structured.brand == "贝贝优"
    assert r.product_category == "milk"


# ---------------------------------------------------------------------------
# I3 多源归一到同一结构（统一接口）
# ---------------------------------------------------------------------------
def _i3_unified():
    md = _tmp_md()
    txt = _tmp_txt()
    milk = MarkdownProductAdapter([md]).fetch()
    text = TextAdapter(txt, title="睿护商品纯文本").fetch()
    assert milk and text
    for r in list(milk) + list(text):
        assert isinstance(r, KnowledgeRecord), "所有来源都应是 KnowledgeRecord"
        for attr in ("source_type", "title", "content", "metadata", "lang",
                     "product_category", "structured"):
            assert hasattr(r, attr), f"KnowledgeRecord 缺字段 {attr}"


# ---------------------------------------------------------------------------
# I4 跨运行内容哈希去重
# ---------------------------------------------------------------------------
def _i4_dedup():
    txt = _tmp_txt()
    db = os.path.join(tempfile.mkdtemp(), "ingest.db")
    store = KnowledgeStore(db, embedding_kind="mock")
    pipe = IngestPipeline(store, "ent_dedup", dedup=True)
    first = pipe.run(TextAdapter(txt, title="t"), name="text")
    assert first > 0, "首次入库应 > 0"
    second = pipe.run(TextAdapter(txt, title="t"), name="text")
    assert second == 0, f"同内容二次入库应去重为 0，实际: {second}"
    # 去重是基于 store 持久化的，换一个新 pipeline 实例仍应去重
    pipe2 = IngestPipeline(store, "ent_dedup", dedup=True)
    third = pipe2.run(TextAdapter(txt, title="t"), name="text")
    assert third == 0, f"跨管线实例仍应去重，实际: {third}"


# ---------------------------------------------------------------------------
# I5 容错：单适配器失败不中断整批、失败留痕、兄弟适配器仍入库
# ---------------------------------------------------------------------------
def _i5_resilient():
    db = os.path.join(tempfile.mkdtemp(), "resilient.db")
    store = KnowledgeStore(db, embedding_kind="mock")
    pipe = IngestPipeline(store, "ent_res", dedup=True)

    class _BoomAdapter:
        def fetch(self):
            raise RuntimeError("采集源不可达（模拟网络故障）")

    md = _tmp_md()
    good = MarkdownProductAdapter([md])
    boom = _BoomAdapter()

    n_good = pipe.run(good, name="markdown")
    n_boom = pipe.run(boom, name="boom")
    assert n_good > 0, "正常适配器应成功入库"
    assert n_boom == 0, "失败适配器应返回 0 且不崩溃"
    assert pipe.failures, "失败应被记录到 failures（不静默丢弃）"
    assert pipe.failures[-1]["source"] == "boom"
    assert "不可达" in pipe.failures[-1]["error"]


# ---------------------------------------------------------------------------
# I6 集成：markdown 走管线入真实 store，产品落表且可被检索
# ---------------------------------------------------------------------------
def _i6_integration():
    db = os.path.join(tempfile.mkdtemp(), "integ.db")
    store = KnowledgeStore(db, embedding_kind="mock")
    pipe = IngestPipeline(store, "ent_integ", dedup=True)
    md = _tmp_md()
    n = pipe.run(MarkdownProductAdapter([md]), name="markdown")
    assert n > 0, "应至少入库 1 条"

    # 结构化产品确实落到 products_milk
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT name, brand FROM products_milk WHERE enterprise_id=?",
            ("ent_integ",),
        ).fetchone()
    assert row is not None, "产品应落入 products_milk 表"
    assert row["name"] == "睿护婴儿配方奶粉1段"

    # 桥接既有 MOD-kb：同一 store 的 retrieve 能命中该产品（不改动检索逻辑）
    hits = store.retrieve("睿护婴儿配方奶粉1段", "ent_integ", top_k=3)
    assert hits, "入库产品应可被 retrieve 命中"
    assert any("睿护" in (h.title or "") for h in hits), "命中结果应含该商品名"


CHECKS = [
    ("I2 markdown 适配器", _i2_markdown),
    ("I3 多源归一统一结构", _i3_unified),
    ("I4 跨运行内容哈希去重", _i4_dedup),
    ("I5 单源失败不中断+留痕", _i5_resilient),
    ("I6 集成：入表且可被检索", _i6_integration),
]


def main():
    failed = []
    for name, fn in CHECKS:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {name}: 异常 {type(e).__name__}: {e}")
            failed.append(name)
    print(f"=== Summary: {len(CHECKS) - len(failed)}/{len(CHECKS)} passed ===")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("RESULT: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
