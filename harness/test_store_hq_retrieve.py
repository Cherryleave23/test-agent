#!/usr/bin/env python3
# @module ingest
"""F6 回归：总部知识库（hq_kb）跨企业可读，且企业间 b_kb 隔离不被破坏。

背景（F6 根因）：retrieve() 第 4 步回查 corpus 时原条件
    if row["enterprise_id"] is not None and row["enterprise_id"] != enterprise_id:
        continue
把 HQ 行（enterprise_id='hq'）当成「异企业」直接丢弃，导致总部资料库
对全部企业 agent 都不可读——废掉了「总部资料库一键共享」设计。
第 1 步 Chroma 召回虽已纳入 HQ，但第 4 步又将其丢弃，召回结果被浪费。

本测试真实运行 KnowledgeStore（mock 嵌入）证明：
  F6a  本企业 ent_A 检索 HQ 文章（kind=article）可命中，hit.enterprise_id=='hq'
  F6b  异企业 ent_B 检索同一 HQ 文章亦可命中（跨企业共享，F6 核心修复点）
  F6c  异企业 ent_B 检索 ent_A 的 b_kb（kind=product_text）不可命中（隔离不被破坏）
  F6d  本企业检索 HQ 原料深度文章（kind=ingredient）可命中，meta.kind 存活

直接运行：python3 test_store_hq_retrieve.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from kb.store import KnowledgeStore, HQ_ENT  # noqa: E402


def main():
    fails = []
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    store = KnowledgeStore(db_path, embedding_kind="mock", rerank_kind="none")

    # 总部知识库（共享）：文章 + 原料深度文
    store.add_hq_knowledge(
        "新生儿睡眠", "新生儿睡眠周期短，按需喂养更安稳。",
        meta={"kind": "article", "vendor": "hq"},
    )
    store.add_hq_knowledge(
        "乳铁蛋白深度", "乳铁蛋白 LF 提升肠道免疫力，是核心免疫蛋白。",
        meta={"kind": "ingredient", "source": "pdf"},
    )
    # 企业 A 自有 b_kb（绑定结构化产品 product_id=5）
    store.add_knowledge(
        "ent_A", "星飞帆1段卖点", "星飞帆1段卖点：小分子好吸收，亲和人体。",
        meta={"kind": "product_text", "product": "星飞帆1段"}, product_id=5,
    )

    def titles_by_ent(hits, ent):
        return [h.title for h in hits if h.enterprise_id == ent]

    # F6a: 本企业 ent_A 可命中 HQ 文章
    hits_a = store.retrieve("新生儿睡眠周期", "ent_A", top_k=5)
    hq_titles_a = titles_by_ent(hits_a, HQ_ENT)
    if "新生儿睡眠" not in hq_titles_a:
        fails.append(f"F6a: ent_A 未命中 HQ 文章，hits={[h.title for h in hits_a]}")
    else:
        hq_hit = next(h for h in hits_a if h.title == "新生儿睡眠")
        if hq_hit.meta.get("kind") != "article":
            fails.append(f"F6a: HQ 命中 hit.meta.kind 应为 article，实际 {hq_hit.meta.get('kind')}")

    # F6b: 异企业 ent_B 也能命中同一 HQ 文章（跨企业共享）
    hits_b = store.retrieve("新生儿睡眠周期", "ent_B", top_k=5)
    hq_titles_b = titles_by_ent(hits_b, HQ_ENT)
    if "新生儿睡眠" not in hq_titles_b:
        fails.append(f"F6b: ent_B 跨企业未命中 HQ 文章（F6 未修复），hits={[h.title for h in hits_b]}")

    # F6c: 隔离不被破坏 —— 同一查询「吸收」（领域词，仅 ent_A 自有 b_kb 含此 token），
    #      ent_A 能读自有 b_kb，ent_B 读不到（HQ 文章不含「吸收」，故 ent_B 不命中 ent_A）。
    ctrl_a = store.retrieve("吸收", "ent_A", top_k=5)
    if "星飞帆1段卖点" not in [h.title for h in ctrl_a if h.enterprise_id == "ent_A"]:
        fails.append(f"F6c: 对照失败，ent_A 自己读不到自有 b_kb，hits={[h.title for h in ctrl_a]}")
    iso_b = store.retrieve("吸收", "ent_B", top_k=5)
    leaked = [h.title for h in iso_b if h.enterprise_id == "ent_A"]
    if leaked:
        fails.append(f"F6c: ent_B 越权读到 ent_A 的 b_kb，leaked={leaked}")

    # F6d: 本企业可读 HQ 原料深度文，meta.kind 存活
    hits_ing = store.retrieve("乳铁蛋白 肠道免疫", "ent_A", top_k=5)
    hq_ing = [h for h in hits_ing if h.title == "乳铁蛋白深度"]
    if not hq_ing:
        fails.append(f"F6d: 未命中 HQ 原料深度文，hits={[h.title for h in hits_ing]}")
    elif hq_ing[0].meta.get("kind") != "ingredient":
        fails.append(f"F6d: HQ 原料命中 hit.meta.kind 应为 ingredient，实际 {hq_ing[0].meta.get('kind')}")

    # 汇总
    for name in ("F6a", "F6b", "F6c", "F6d"):
        status = "FAIL" if any(name in f for f in fails) else "PASS"
        print(f"[{status}] {name}")
    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL GREEN (F6 HQ cross-enterprise retrieve fixed)")
    sys.exit(0)


if __name__ == "__main__":
    main()
