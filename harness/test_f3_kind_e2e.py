#!/usr/bin/env python3
# @module ingest + agent
"""F3 端到端回归（D1+D2）：让 F3 的 kind 路由/加权在真实链路生效。

背景：此前 F3 是「假绿」——
  D1：importer._load_corpus 只读 meta，不读 corpus.ndjson 顶层 kind → Chroma 中 kind 恒为 ""；
  D2：agent.pipeline 检索从不传 kind_filter/kind_weight → 即便 kind 进了 Chroma 也不路由。
本测试真实运行 scan_and_load（模拟 B端数据处理→入库），证明：
  F3e  corpus 的 kind（product_text/article/ingredient）经 importer 正确进入 Chroma 元数据
  F3f  agent.answer 对明确意图的查询会把 kind_weight 传入 store.retrieve（F3 路由端到端打通）

直接运行：python3 test_f3_kind_e2e.py  → 退出码 0 全过，非 0 有失败。
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from kb.store import KnowledgeStore  # noqa: E402
from ingest.importer import scan_and_load  # noqa: E402
from common.config import EnterpriseConfig, EmbeddingConfig, LLMConfig  # noqa: E402
from agent.pipeline import Agent, classify_intent, intent_kind_weight  # noqa: E402


def _write_bundle(bundle_dir, ent):
    os.makedirs(bundle_dir, exist_ok=True)
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
    fails = []
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "kb.db")
    inbox = os.path.join(tmp, "inbox")
    os.makedirs(inbox, exist_ok=True)
    ent = "ent_X"

    store = KnowledgeStore(db_path, embedding_kind="mock", rerank_kind="none")
    _write_bundle(os.path.join(inbox, "b1"), ent)
    scan_and_load(inbox, store, ent)

    # F3e：直接查 Chroma 元数据，确认 kind 已落库（D1 修复）
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = {r["id"]: r["title"] for r in
                conn.execute("SELECT id, title FROM corpus WHERE enterprise_id=?", (ent,))}
    got = store.collection.get(where={"enterprise_id": ent}, include=["metadatas"])
    empties = 0
    for mid, meta in zip(got["ids"], got["metadatas"]):
        cid = int(mid)
        k = meta.get("kind", "")
        if not k:
            empties += 1
            fails.append(f"F3e: corpus '{rows.get(cid, cid)}' Chroma.kind 为空（D1 未修复）")
    if empties == 0:
        print(f"[PASS] F3e（{len(got['ids'])} 条 corpus 的 kind 均已进入 Chroma）")

    # F3f：agent.answer 对明确意图查询应把 kind_weight 传入 retrieve（D2 修复）
    cfg = EnterpriseConfig(enterprise_id=ent, db_path=db_path,
                           embedding=EmbeddingConfig(kind="mock"),
                           llm=LLMConfig(kind="mock"))
    captured = {}
    orig_retrieve = store.retrieve

    def _spy(query, enterprise_id, top_k=5, filters=None, kind_filter=None, kind_weight=None):
        captured["kind_weight"] = kind_weight
        captured["kind_filter"] = kind_filter
        return []

    store.retrieve = _spy
    agent = Agent(cfg, store)

    # 成分意图：应检测到 ingredient 并加权
    import asyncio
    asyncio.run(agent.answer("这个 DHA 成分有什么作用"))
    if not captured.get("kind_weight"):
        fails.append("F3f: 成分意图查询未把 kind_weight 传入 retrieve（D2 未修复）")
    else:
        print(f"[PASS] F3f（意图→kind_weight={captured['kind_weight']}）")

    # 无意图：应不传 kind_weight
    captured.clear()
    asyncio.run(agent.answer("你好"))
    if captured.get("kind_weight"):
        fails.append(f"F3f: 无意图查询不应传 kind_weight，实际 {captured['kind_weight']}")
    else:
        print("[PASS] F3f(none)")

    # 单元：classify_intent 映射正确
    if classify_intent("这个成分含量多少") != "ingredient":
        fails.append("F3f: classify_intent(成分) 应=ingredient")
    if classify_intent("辅食怎么添加") != "article":
        fails.append("F3f: classify_intent(辅食) 应=article")
    if classify_intent("这款产品卖点是什么") != "product_text":
        fails.append("F3f: classify_intent(卖点) 应=product_text")
    if classify_intent("你好") is not None:
        fails.append("F3f: classify_intent(你好) 应=None")
    if not fails:
        print("[PASS] F3f(intent 映射)")

    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL GREEN (F3 kind 端到端生效：importer 落 kind + agent 路由)")
    sys.exit(0)


if __name__ == "__main__":
    main()
