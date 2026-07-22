#!/usr/bin/env python3
# @module ingest
"""F2 回归：HQ 知识库（厂商分发）实例只读护栏。

背景（F2）：HQ 共享库由厂商分发，实例侧必须只读 —— 企业本地不得删除/改写，
避免污染共享总部知识。F6 已修复 HQ 跨企业可读，本测试补"只读"强制。

本测试真实运行 KnowledgeStore 证明：
  F2a  add_hq_knowledge 行被护栏判定只读（ent='hq'、part=hq_kb，meta 保持内容语义纯净）
  F2b  delete_corpus 删 HQ 行 → 抛 ReadonlyError（拒绝）
  F2c  update_corpus 改写 HQ 行 → 抛 ReadonlyError（拒绝）
  F2d  企业自有 b_kb 行可正常 delete / update（护栏不误伤）
  F2e  HQ 分区只读不影响 retrieve 跨企业可读（F6 仍成立）

直接运行：python3 test_store_hq_readonly.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from kb.store import KnowledgeStore, HQ_ENT, ReadonlyError  # noqa: E402


def main():
    fails = []
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    store = KnowledgeStore(db_path, embedding_kind="mock", rerank_kind="none")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # F2a：HQ 行被护栏识别为只读（enterprise_id='hq' 即只读，meta 保持内容语义纯净）
    hq_cid = store.add_hq_knowledge(
        "新生儿睡眠", "新生儿睡眠周期短，按需喂养更安稳。",
        meta={"kind": "article", "vendor": "hq"},
    )
    r = conn.execute(
        "SELECT enterprise_id, part, meta_json FROM corpus WHERE id=?", (hq_cid,)
    ).fetchone()
    readonly = KnowledgeStore._row_readonly(
        {"enterprise_id": r["enterprise_id"], "meta_json": r["meta_json"]}
    )
    if r["enterprise_id"] != HQ_ENT or r["part"] != "hq_kb" or not readonly:
        fails.append(f"F2a: HQ 行应 ent=hq/part=hq_kb 且被护栏判定只读，实际 {tuple(r)}")
    else:
        print("[PASS] F2a")

    # F2b：删 HQ 行被拒
    try:
        store.delete_corpus(hq_cid)
        fails.append("F2b: 删除 HQ 行未拒绝（应抛 ReadonlyError）")
    except ReadonlyError:
        print("[PASS] F2b")
    except Exception as e:
        fails.append(f"F2b: 抛错类型错误 {type(e).__name__}: {e}")
    # 确认仍在
    if conn.execute("SELECT 1 FROM corpus WHERE id=?", (hq_cid,)).fetchone() is None:
        fails.append("F2b: HQ 行被误删")

    # F2c：改写 HQ 行被拒
    try:
        store.update_corpus(hq_cid, title="篡改标题")
        fails.append("F2c: 改写 HQ 行未拒绝（应抛 ReadonlyError）")
    except ReadonlyError:
        print("[PASS] F2c")
    except Exception as e:
        fails.append(f"F2c: 抛错类型错误 {type(e).__name__}: {e}")

    # F2d：企业自有 b_kb 行可删/改
    b_cid = store.add_knowledge("ent_A", "星飞帆卖点", "小分子好吸收。")
    store.update_corpus(b_cid, title="星飞帆卖点(改)")
    r2 = conn.execute("SELECT title FROM corpus WHERE id=?", (b_cid,)).fetchone()
    if r2["title"] != "星飞帆卖点(改)":
        fails.append(f"F2d: 企业行改写未生效，实际 {r2['title']!r}")
    else:
        store.delete_corpus(b_cid)
        if conn.execute("SELECT 1 FROM corpus WHERE id=?", (b_cid,)).fetchone() is not None:
            fails.append("F2d: 企业行删除未生效")
        else:
            print("[PASS] F2d")

    # F2e：HQ readonly 标记不影响跨企业可读（F6）
    hits = store.retrieve("新生儿睡眠", "ent_B", top_k=5)
    if not any(h.enterprise_id == HQ_ENT for h in hits):
        fails.append(f"F2e: HQ 仍应跨企业可读，hits={[h.title for h in hits]}")
    else:
        print("[PASS] F2e")

    conn.close()

    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL GREEN (F2 HQ 只读护栏)")
    sys.exit(0)


if __name__ == "__main__":
    main()
