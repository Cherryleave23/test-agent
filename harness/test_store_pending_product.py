#!/usr/bin/env python3
# @module ingest
"""F5 回归：待确认商品（pending）确认/删除数据侧基础。

背景（F5）：resolver 产 status=pending（无 reg_number 匹配）的商品需在企业端确认/合并/删除，
此前无闭环。本测试锁 store 数据侧原语（微信侧 UX 接线为后续跨模块待办）：
  F5a  list_pending_products 只返回 reg_number 为空的 pending 商品（confirmed 不混入）
  F5b  confirm_product 写 reg_number 后该商品不再 pending
  F5c  delete_product 删除商品及其绑定的语料分块（b_milk/b_nutrition，按 product_id）

直接运行：python3 test_store_pending_product.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from kb.store import KnowledgeStore  # noqa: E402
from kb.models import MilkProduct  # noqa: E402


def main():
    fails = []
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    store = KnowledgeStore(db_path, embedding_kind="mock", rerank_kind="none")
    ent = "ent_X"

    pending = MilkProduct(enterprise_id=ent, name="待确认A", brand="星飞帆",
                          stage="1段", age_range="0-6个月", price=368.0, origin="中国",
                          milk_origin="新西兰", ptype="牛奶粉", reg_number="",  # 空→pending
                          manufacturer="星飞帆乳业", ingredients="生牛乳", nutrition="蛋白质12",
                          highlights="OPO")
    confirmed = MilkProduct(enterprise_id=ent, name="已确认B", brand="贝贝优",
                            stage="1段", age_range="0-6个月", price=368.0, origin="中国",
                            milk_origin="新西兰", ptype="牛奶粉",
                            reg_number="国食注字YP20180012", manufacturer="贝贝优",
                            ingredients="生牛乳", nutrition="蛋白质12", highlights="OPO")
    pid_pending = store.add_milk(pending)
    pid_confirmed = store.add_milk(confirmed)

    # F5a：list_pending 只含 pending
    pend = store.list_pending_products(ent)
    pids = {p["id"] for p in pend}
    if pid_pending not in pids:
        fails.append(f"F5a: pending 商品应出现在列表，实际 {pids}")
    elif pid_confirmed in pids:
        fails.append("F5a: confirmed 商品不应出现在 pending 列表")
    else:
        print("[PASS] F5a")

    # F5b：confirm 后不再 pending
    store.confirm_product(pid_pending, "国食注字YP20199999", table="products_milk")
    pend2 = store.list_pending_products(ent)
    if any(p["id"] == pid_pending for p in pend2):
        fails.append("F5b: 确认后商品仍出现在 pending 列表")
    else:
        print("[PASS] F5b")

    # F5c：delete_product 删商品 + 绑定语料分块
    # 先为该 pending 商品补一条 b_milk 分块（add_milk 已内部建分块；这里直接验证删除连带）
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    n_chunks_before = conn.execute(
        "SELECT COUNT(*) cnt FROM corpus WHERE product_id=?", (pid_confirmed,)).fetchone()["cnt"]
    store.delete_product(pid_confirmed, table="products_milk")
    n_prod = conn.execute(
        "SELECT COUNT(*) cnt FROM products_milk WHERE id=?", (pid_confirmed,)).fetchone()["cnt"]
    n_chunks_after = conn.execute(
        "SELECT COUNT(*) cnt FROM corpus WHERE product_id=?", (pid_confirmed,)).fetchone()["cnt"]
    conn.close()
    if n_prod != 0:
        fails.append(f"F5c: 商品未删除，剩余 {n_prod}")
    elif n_chunks_before and n_chunks_after != 0:
        fails.append(f"F5c: 绑定语料分块未删除（before={n_chunks_before}, after={n_chunks_after}）")
    else:
        print("[PASS] F5c")

    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL GREEN (F5 待确认商品 确认/删除 数据侧基础)")
    sys.exit(0)


if __name__ == "__main__":
    main()
