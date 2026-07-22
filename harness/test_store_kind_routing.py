#!/usr/bin/env python3
# @module ingest
"""F3 回归：retrieve 按 `meta.kind` 路由/加权（产品问答 vs 育儿知识 vs 成分机制分流）。

背景（F3）：bundle 契约 corpus.ndjson 用 `kind` ∈ {product_text, article, ingredient}
标记内容类型，但 retrieve 侧此前未用 `kind` 做路由/加权，产品问答与育儿知识混流。
本测试证明 store 侧基础已就位（Chroma metadata 带 `kind` + retrieve 支持 kind_filter/kind_weight）：
  F3a  不同 kind 语料落库后 Chroma metadata.kind 正确（product_text/article/ingredient）
  F3b  kind_filter=['article'] 只返回 article，product_text 被路由排除
  F3c  kind_weight={'product_text':5.0} 把 product_text 加权推到首位（默认并列时按其插入序）
  F3d  默认不传 kind 参数 → 两种 kind 均召回（向后兼容，不改变旧行为）

agent 侧「意图识别→选 kind」属 MOD-agent 协调，本测试只锁 store 侧能力。

直接运行：python3 test_store_kind_routing.py  → 退出码 0 全过，非 0 有失败。
"""
import json
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from kb.store import KnowledgeStore  # noqa: E402


def chroma_kind(store, cid):
    md = store.collection.get(ids=[str(cid)], include=["metadatas"])["metadatas"]
    return (md[0] or {}).get("kind", "") if md else ""


def main():
    fails = []
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    store = KnowledgeStore(db_path, embedding_kind="mock", rerank_kind="none")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 共享域 token "吸收"：两篇不同 kind 内容都含它，查询可同时召回（重叠度相同 → 并列）
    # 先插 article，再插 product_text（默认并列时插入序 article 在前）
    a_cid = store.add_knowledge(
        "ent_X", "辅食添加时机", "宝宝 好吸收 辅食 添加 时机 按需喂养",
        meta={"kind": "article"},
    )
    p_cid = store.add_knowledge(
        "ent_X", "星飞帆卖点", "星飞帆 小分子 好吸收 易溶解 亲和",
        meta={"kind": "product_text"},
    )

    # F3a：Chroma metadata.kind 正确
    ka = chroma_kind(store, a_cid)
    kp = chroma_kind(store, p_cid)
    if ka != "article":
        fails.append(f"F3a: article 行 Chroma kind 应为 article，实际 {ka!r}")
    elif kp != "product_text":
        fails.append(f"F3a: product_text 行 Chroma kind 应为 product_text，实际 {kp!r}")
    else:
        print("[PASS] F3a")

    # F3b：kind_filter 路由 —— 只返回 article
    hits = store.retrieve("吸收", "ent_X", top_k=5, kind_filter=["article"])
    kinds = [h.meta.get("kind") for h in hits]
    if not kinds or any(k != "article" for k in kinds):
        fails.append(f"F3b: kind_filter=['article'] 应只返 article，实际 {kinds}")
    elif any(h.id == p_cid for h in hits):
        fails.append("F3b: product_text 被路由排除，却仍出现")
    else:
        print("[PASS] F3b")

    # F3c：kind_weight 加权 —— product_text 推到首位（默认并列时 article 在前）
    base = store.retrieve("吸收", "ent_X", top_k=5)  # 默认：并列，按插入序 article 可能居首
    base_top = base[0].meta.get("kind") if base else None
    weighted = store.retrieve("吸收", "ent_X", top_k=5,
                              kind_weight={"product_text": 5.0})
    w_top = weighted[0].meta.get("kind") if weighted else None
    if w_top != "product_text":
        fails.append(f"F3c: kind_weight 应把 product_text 推首位，实际 top={w_top!r}（base_top={base_top!r}）")
    else:
        print("[PASS] F3c")

    # F3d：默认向后兼容 —— 两种 kind 都召回
    hits_all = store.retrieve("吸收", "ent_X", top_k=5)
    all_kinds = {h.meta.get("kind") for h in hits_all}
    if "article" not in all_kinds or "product_text" not in all_kinds:
        fails.append(f"F3d: 默认应两种 kind 均召回，实际 {all_kinds}")
    else:
        print("[PASS] F3d")

    conn.close()

    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL GREEN (F3 retrieve 按 kind 路由/加权)")
    sys.exit(0)


if __name__ == "__main__":
    main()
