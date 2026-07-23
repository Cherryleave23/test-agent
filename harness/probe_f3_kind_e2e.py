#!/usr/bin/env python3
"""真实部署探针 P1：F3 kind 在 importer 真实入库链路是否落入 Chroma。

模拟「B端数据处理 → bundle → scan_and_load 入库」真实路径，直接查 Chroma 元数据，
证明 corpus.ndjson 顶层 kind（product_text/article/ingredient）是否在 importer 处丢失。

运行：python3 harness/probe_f3_kind_e2e.py
依赖：仅 mock embedding（无需 PaddleOCR / sentence_transformers）。
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from kb.store import KnowledgeStore  # noqa: E402
from ingest.importer import scan_and_load  # noqa: E402


def _write_bundle(bundle_dir, ent):
    os.makedirs(bundle_dir, exist_ok=True)
    # 一条 product_text（应绑商品），一条 article（主题级），一条 ingredient（成分级）
    corpus = [
        {"part": "b_kb", "kind": "product_text", "title": "星飞帆卖点",
         "content": "小分子好吸收 易溶解", "product_uid": "reg:YP20180012",
         "meta": {"source": "md", "path": "产品资料/星飞帆/1段.md"}},
        {"part": "b_kb", "kind": "article", "title": "辅食添加时机",
         "content": "宝宝 好吸收 辅食 添加 时机", "meta": {"source": "md"}},
        {"part": "b_kb", "kind": "ingredient", "title": "DHA作用",
         "content": "DHA 助脑发育 视力", "meta": {"source": "md"}},
    ]
    manifest = {
        "schema_version": "1.0", "enterprise_id": ent, "tool_version": "dataproc 0.1.0",
        "generated_at": "2026-07-21T00:00:00", "counts": {"corpus": 3},
        "checksums": {}, "structuring_provider": "rule-only",
    }
    with open(os.path.join(bundle_dir, "corpus.ndjson"), "w", encoding="utf-8") as f:
        for r in corpus:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(bundle_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def main():
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "kb.db")
    inbox = os.path.join(tmp, "inbox")
    os.makedirs(inbox, exist_ok=True)
    ent = "ent_X"

    store = KnowledgeStore(db_path, embedding_kind="mock", rerank_kind="none")
    _write_bundle(os.path.join(inbox, "b1"), ent)
    res = scan_and_load(inbox, store, ent)
    print(f"[入库结果] loaded={[r['bundle'] for r in res['loaded']]} "
          f"errors={res['errors']}")

    # 直接查 Chroma 元数据里的 kind 字段（真实 RAG 检索就是按这个 kind 做路由/加权）
    got = store.collection.get(where={"enterprise_id": ent}, include=["metadatas"])
    # title 存在 content 表，不在 Chroma meta；用 SQLite 反查 title 以便对照
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = {r["id"]: r["title"] for r in
                conn.execute("SELECT id, title FROM corpus WHERE enterprise_id=?", (ent,))}

    print(f"\n[Chroma 元数据] 该租户 corpus 向量数 = {len(got['ids'])}")
    print(f"{'cid':<6}{'title':<14}{'Chroma.kind':<14}{'结论'}")
    print("-" * 56)
    bad = 0
    for mid, meta in zip(got["ids"], got["metadatas"]):
        cid = int(mid)
        k = meta.get("kind", "<缺失>")
        if cid not in rows:
            rows[cid] = f"id={cid}"
        title = rows.get(cid, "?")
        ok = k not in ("", None, "<缺失>")
        if not ok:
            bad += 1
        print(f"{cid:<6}{title:<14}{str(k):<14}{'❌ 丢失' if not ok else '✅'}")

    print("\n" + "=" * 50)
    if bad:
        print(f"❌ 实证结论：{bad}/{len(got['ids'])} 条 corpus 在 Chroma 中 kind 为空，"
              f"F3 kind 路由/加权在真实入库链路完全失效（importer 只读 meta，未读顶层 kind）。")
        sys.exit(1)
    else:
        print("✅ 实证结论：所有 corpus 的 kind 已正确进入 Chroma。")
        sys.exit(0)


if __name__ == "__main__":
    main()
