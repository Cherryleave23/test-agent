#!/usr/bin/env python3
# @module ingest
"""F4 回归：dataproc bundle 加载器 + 收件箱触发（谁触发 load_bundle）。

背景（F4）：此前无把 tools/dataproc 产出 bundle 灌入 store 的加载器，且「谁触发」未定义。
本测试真实运行 importer + KnowledgeStore 证明：
  F4a  load_bundle 把 corpus.ndjson 的 b_kb / hq_kb 行都落库
  F4b  product_text 语料按 product_uid→product_id 绑定（uid→pid 映射生效）
  F4c  hq_products.ndjson 播种进 HQ 商品库（get_hq_products 非空）
  F4d  scan_and_load 成功包移入 processed/，二次扫描不再加载（移动即幂等）
  F4e  manifest 存在但 enterprise_id 不匹配的非法包被移到 failed/ 并留痕
  F4f  无 manifest 的目录视为上传中跳过（留 inbox，不加载不崩溃）

直接运行：python3 test_ingest_bundle_load.py  → 退出码 0 全过，非 0 有失败。
"""
import json
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from kb.store import KnowledgeStore  # noqa: E402
from ingest.importer import load_bundle, scan_and_load, load_on_startup  # noqa: E402


def _write_bundle(bundle_dir, ent):
    os.makedirs(bundle_dir, exist_ok=True)
    milk_fields = {
        "name": "星飞帆1段", "brand": "星飞帆", "stage": "1段", "age_range": "0-6个月",
        "price": 368.0, "origin": "中国", "milk_origin": "新西兰", "ptype": "牛奶粉",
        "reg_number": "国食注字YP20180012", "manufacturer": "星飞帆乳业",
        "ingredients": "生牛乳、脱盐乳清粉", "nutrition": "蛋白质12.5g/100g",
        "highlights": "含OPO结构脂",
    }
    products = [{
        "kind": "milk", "uid": "reg:YP20180012", "status": "confirmed",
        "source_ref": "产品资料/星飞帆/1段.md", "resolved": {"match": "reg_number"},
        "fields": milk_fields,
    }]
    corpus = [
        {  # product_text 绑定商品（验证 uid→pid）
            "part": "b_kb", "kind": "product_text", "title": "星飞帆卖点",
            "content": "小分子好吸收 易溶解", "product_uid": "reg:YP20180012",
            "meta": {"source": "md", "path": "产品资料/星飞帆/1段.md"},
        },
        {  # 主题级 article（不绑商品）
            "part": "b_kb", "kind": "article", "title": "辅食添加时机",
            "content": "宝宝 好吸收 辅食 添加 时机", "meta": {"source": "md"},
        },
        {  # HQ 共享库
            "part": "hq_kb", "kind": "article", "title": "新生儿睡眠",
            "content": "新生儿睡眠周期短 按需喂养更安稳", "meta": {"vendor": "hq"},
        },
    ]
    hq_products = [{
        "kind": "milk",
        "fields": {"name": "厂商主推1段", "brand": "厂商", "reg_number": "国食注字YP20170001"},
        "meta": {"vendor": "hq"},
    }]
    manifest = {
        "schema_version": "1.0", "enterprise_id": ent, "tool_version": "dataproc 0.1.0",
        "generated_at": "2026-07-21T00:00:00", "counts": {"products": 1, "corpus": 3},
        "checksums": {}, "structuring_provider": "rule-only",
    }
    with open(os.path.join(bundle_dir, "products.ndjson"), "w", encoding="utf-8") as f:
        for r in products:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(bundle_dir, "corpus.ndjson"), "w", encoding="utf-8") as f:
        for r in corpus:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(bundle_dir, "hq_products.ndjson"), "w", encoding="utf-8") as f:
        for r in hq_products:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(bundle_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def main():
    fails = []
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "kb.db")
    inbox = os.path.join(tmp, "inbox")
    os.makedirs(inbox, exist_ok=True)

    store = KnowledgeStore(db_path, embedding_kind="mock", rerank_kind="none")
    ent = "ent_X"

    # 构造合法 bundle 并放入收件箱
    _write_bundle(os.path.join(inbox, "b1"), ent)

    # F4a/b/c：scan_and_load 加载 b1（加载 + 移动）
    res = scan_and_load(inbox, store, ent)
    if len(res["loaded"]) != 1 or res["loaded"][0]["bundle"] != "b1":
        fails.append(f"F4a: 应加载 1 个 bundle(b1)，实际 {res['loaded']}")
    else:
        print("[PASS] F4a(加载)")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        parts = dict((r["part"], r["cnt"]) for r in conn.execute(
            "SELECT part, COUNT(*) cnt FROM corpus GROUP BY part"))
        # F4a：b_kb 与 hq_kb 均落库
        if parts.get("b_kb", 0) < 1 or parts.get("hq_kb", 0) < 1:
            fails.append(f"F4a: corpus 应含 b_kb 与 hq_kb，实际 {parts}")
        else:
            print("[PASS] F4a(落库)")

        # F4b：product_text 语料 product_id == milk 产品 pid
        pid = conn.execute(
            "SELECT id FROM products_milk WHERE name=?", ("星飞帆1段",)).fetchone()
        cid_row = conn.execute(
            "SELECT product_id FROM corpus WHERE title=?", ("星飞帆卖点",)).fetchone()
        if pid is None:
            fails.append("F4b: milk 产品未入库")
        elif cid_row is None or cid_row["product_id"] != pid["id"]:
            fails.append(f"F4b: product_text 应绑定 pid={pid['id'] if pid else None}，"
                         f"实际 {None if cid_row is None else cid_row['product_id']}")
        else:
            print("[PASS] F4b")

        # F4c：HQ 商品库被播种
        hq = conn.execute("SELECT COUNT(*) cnt FROM hq_products").fetchone()
        if hq["cnt"] < 1:
            fails.append(f"F4c: hq_products 应非空，实际 {hq['cnt']}")
        else:
            print("[PASS] F4c")

    # F4d：二次扫描——b1 已移入 processed，应不再加载（幂等）
    res2 = scan_and_load(inbox, store, ent)
    if res2["loaded"]:
        fails.append(f"F4d: 二次扫描应空加载，实际 {res2['loaded']}")
    elif not os.path.isdir(os.path.join(inbox, "processed", "b1")):
        fails.append("F4d: b1 应已在 processed/")
    else:
        print("[PASS] F4d")

    # F4e：manifest 存在但 enterprise_id 不匹配 → 移到 failed/，留痕
    bad = os.path.join(inbox, "bad")
    os.makedirs(bad, exist_ok=True)
    with open(os.path.join(bad, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"schema_version": "1.0", "enterprise_id": "ent_OTHER",
                   "tool_version": "x", "counts": {}, "checksums": {}}, f)
    with open(os.path.join(bad, "corpus.ndjson"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"part": "b_kb", "kind": "article",
                            "title": "x", "content": "y"}) + "\n")
    res3 = scan_and_load(inbox, store, ent)
    if not res3["failed"] or not os.path.isdir(os.path.join(inbox, "failed", "bad")):
        fails.append(f"F4e: 不匹配包应移入 failed/，实际 {res3}")
    elif not res3["errors"]:
        fails.append("F4e: 应记录错误留痕")
    else:
        print("[PASS] F4e")

    # F4f：无 manifest 的目录 → 跳过（留 inbox，视为上传中，不加载不崩溃）
    partial = os.path.join(inbox, "partial")
    os.makedirs(partial, exist_ok=True)
    with open(os.path.join(partial, "corpus.ndjson"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"part": "b_kb", "kind": "article",
                            "title": "p", "content": "q"}) + "\n")
    res4 = scan_and_load(inbox, store, ent)
    if res4["loaded"] or res4["failed"]:
        fails.append(f"F4f: 无 manifest 目录应跳过，实际 {res4}")
    elif not os.path.isdir(partial):
        fails.append("F4f: 无 manifest 目录应留 inbox（上传中），不应被移动")
    else:
        print("[PASS] F4f")

    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL GREEN (F4 bundle 加载器 + 收件箱触发)")
    sys.exit(0)


if __name__ == "__main__":
    main()
