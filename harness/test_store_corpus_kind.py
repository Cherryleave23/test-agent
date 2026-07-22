#!/usr/bin/env python3
# @module ingest
"""F1 回归：corpus 写方法签名与 `kind` 语义去撞（配合 MOD-knowledge-ingest 产物契约）。

背景：产物契约 corpus.ndjson 用 `kind` ∈ {product_text, article, ingredient} 标记内容类型，
但原 store.add_hq_knowledge 硬编码 meta={"kind":"hq_kb"}，与新 `kind` 语义撞车，且
add_knowledge 无 product_id 形参（importer 无法把 product_uid 解析为 product_id 绑定商品）。

本测试真实运行 KnowledgeStore 证明：
  F1a  add_knowledge 带 product_id + meta.kind → corpus 行 part=b_kb、product_id 落库、kind 保留
  F1b  add_hq_knowledge 带 meta={"kind":"article"} → part=hq_kb、meta.kind=article（不再写成 "hq_kb"）
  F1c  add_hq_knowledge(title, content) 旧式 2 参调用 → meta={}（向后兼容，无硬编码 kind）
  F1d  retrieve 跨企业可读 hq_kb 文章，且命中 hit.meta.kind 正确（article/ingredient 区分存活）

直接运行：python3 test_store_corpus_kind.py  → 退出码 0 全过，非 0 有失败。
"""
import json
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from kb.store import KnowledgeStore  # noqa: E402


def row(conn, cid):
    r = conn.execute(
        "SELECT part, enterprise_id, product_id, meta_json FROM corpus WHERE id=?",
        (cid,),
    ).fetchone()
    return r


def main():
    fails = []
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    store = KnowledgeStore(db_path, embedding_kind="mock", rerank_kind="none")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # F1a: add_knowledge 带 product_id + meta.kind
    cid_a = store.add_knowledge(
        "ent_b", "DHA 成分深度", "DHA 促进婴幼儿脑发育，有效原因是…",
        meta={"kind": "ingredient", "source": "pdf"}, product_id=7,
    )
    r = row(conn, cid_a)
    if not (r["part"] == "b_kb" and r["product_id"] == 7):
        fails.append(f"F1a: part/product_id 错误 {tuple(r)}")
    else:
        mk = json.loads(r["meta_json"]).get("kind")
        if mk != "ingredient":
            fails.append(f"F1a: meta.kind 应为 ingredient，实际 {mk}")

    # F1b: add_hq_knowledge 带 meta={"kind":"article"}，不再写成 "hq_kb"
    cid_b = store.add_hq_knowledge(
        "新生儿睡眠", "新生儿睡眠周期短，按需喂养更安稳。",
        meta={"kind": "article", "vendor": "hq"},
    )
    r = row(conn, cid_b)
    if r["part"] != "hq_kb":
        fails.append(f"F1b: part 应为 hq_kb，实际 {r['part']}")
    else:
        mk = json.loads(r["meta_json"]).get("kind")
        if mk != "article":
            fails.append(f"F1b: meta.kind 应为 article（非 hq_kb），实际 {mk}")

    # F1c: 旧式 2 参调用向后兼容
    cid_c = store.add_hq_knowledge("旧式共享条目", "内容")
    r = row(conn, cid_c)
    if json.loads(r["meta_json"]) != {}:
        fails.append(f"F1c: 旧式调用 meta 应为空 dict，实际 {r['meta_json']}")

    # F1d: 同企业 ingredient（b_kb, product_id 绑定）可被 retrieve 命中，且 hit.meta.kind 存活
    # （hq_kb 跨企业可读性已由 F6 修复并单独立 `harness/test_store_hq_retrieve.py` 回归，此处不重复断言）
    hits_ing = store.retrieve("DHA 脑发育 婴幼儿", "ent_b", top_k=5)
    ing_kinds = [h.meta.get("kind") for h in hits_ing]
    if not any(k == "ingredient" for k in ing_kinds):
        fails.append(f"F1d: DHA 查询未命中 ingredient 或 kind 未存活，hits={ing_kinds}")
    if not any(h.product_id == 7 for h in hits_ing):
        fails.append(f"F1d: ingredient 命中块未带 product_id=7，hits={[(h.product_id) for h in hits_ing]}")

    conn.close()

    # 汇总
    for name in ("F1a", "F1b", "F1c", "F1d"):
        status = "FAIL" if any(name in f for f in fails) else "PASS"
        print(f"[{status}] {name}")
    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL GREEN (F1 corpus kind signature fixed)")
    sys.exit(0)


if __name__ == "__main__":
    main()
