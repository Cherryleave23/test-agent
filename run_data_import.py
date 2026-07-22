#!/usr/bin/env python3
"""最终版数据导入工具演示：把 dataproc bundle 灌入 KnowledgeStore。

流程：
  1. 用 dataproc build_bundle 生成 NDJSON bundle
  2. 把 bundle 放入收件箱目录
  3. 调用 scan_and_load 自动加载
  4. 验证知识库内容
"""
import os
import sys
import tempfile
import json

# 设置 path
TEST_AGENT = r"c:\Users\a2287\.trae-cn\work\6a5e45f72a10b8e8c14d3831\test-agent"
sys.path.insert(0, os.path.join(TEST_AGENT, "src"))

BUNDLE_DIR = r"c:\Users\a2287\.trae-cn\work\6a5e45f72a10b8e8c14d3831\dataproc_demo_bundle"
ENTERPRISE_ID = "ent_demo"

def main():
    print("=" * 60)
    print("母婴 Agent 数据导入工具 — 最终版演示")
    print("=" * 60)

    # 1. 创建临时收件箱和数据库
    tmp = tempfile.mkdtemp(prefix="agent_import_")
    inbox = os.path.join(tmp, "inbox")
    bundle_name = "demo_bundle"
    bundle_dest = os.path.join(inbox, bundle_name)
    db_path = os.path.join(tmp, "agent.db")

    # 2. 复制 bundle 到收件箱
    import shutil
    shutil.copytree(BUNDLE_DIR, bundle_dest)
    print(f"\n[1] Bundle 已放入收件箱: {bundle_dest}")

    # 验证 manifest
    with open(os.path.join(bundle_dest, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    print(f"    enterprise_id: {manifest['enterprise_id']}")
    print(f"    counts: {manifest['counts']}")

    # 3. 创建 KnowledgeStore
    from kb.store import KnowledgeStore
    print(f"\n[2] 初始化 KnowledgeStore (db={db_path})")
    store = KnowledgeStore(db_path, embedding_kind="mock", rerank_kind="none")

    # 4. 调用 scan_and_load
    from ingest.importer import scan_and_load
    print(f"\n[3] 执行 scan_and_load...")
    result = scan_and_load(inbox, store, ENTERPRISE_ID)

    print(f"\n[4] 导入结果:")
    print(f"    loaded: {len(result['loaded'])} 个 bundle")
    for item in result["loaded"]:
        stats = item["stats"]
        print(f"      - {item['bundle']}: products={stats['products']}, "
              f"corpus={stats['corpus']}, errors={len(stats['errors'])}")
    print(f"    failed: {len(result['failed'])} 个 bundle")
    if result["errors"]:
        for e in result["errors"]:
            print(f"      error: {e}")

    # 5. 验证知识库
    print(f"\n[5] 验证知识库内容:")
    from common.db import db_tx
    with db_tx(db_path) as conn:
        corpus_count = conn.execute("SELECT COUNT(*) FROM corpus").fetchone()[0]
        products_count = conn.execute("SELECT COUNT(*) FROM products_milk").fetchone()[0]
        print(f"    corpus 总数: {corpus_count}")
        print(f"    products_milk 总数: {products_count}")

        print(f"\n    --- 产品列表 ---")
        rows = conn.execute(
            "SELECT id, name, brand, stage, reg_number FROM products_milk ORDER BY id"
        ).fetchall()
        for r in rows:
            status = "confirmed" if r["reg_number"] else "pending"
            print(f"    [{r['id']}] {r['name']} | {r['brand']} | {r['stage']} | {r['reg_number']} | {status}")

        print(f"\n    --- 语料列表 ---")
        rows = conn.execute(
            "SELECT id, part, title, substr(content,1,50) as preview FROM corpus ORDER BY id"
        ).fetchall()
        for r in rows:
            print(f"    [{r['id']}] ({r['part']}) {r['title']}")
            print(f"         {r['preview']}...")

    # 6. 验证检索
    print(f"\n[6] 测试检索 (mock embedding):")
    hits = store.retrieve("配方奶粉 好吸收", ENTERPRISE_ID, top_k=3)
    print(f"    查询: '配方奶粉 好吸收'")
    print(f"    命中: {len(hits)} 条")
    for h in hits:
        print(f"    - [{h.id}] ({h.part}) {h.title} (score={h.score:.4f})")

    # 7. 清理
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n[7] 临时文件已清理")

    # 汇总
    print(f"\n{'=' * 60}")
    total_loaded = sum(item["stats"]["products"] for item in result["loaded"])
    total_corpus = sum(item["stats"]["corpus"] for item in result["loaded"])
    total_errors = sum(len(item["stats"]["errors"]) for item in result["loaded"])
    print(f"导入完成: {total_loaded} 个产品, {total_corpus} 条语料, {total_errors} 个错误")
    if total_errors == 0:
        print("状态: ALL GREEN")
    else:
        print("状态: 有错误")
    print(f"{'=' * 60}")

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
