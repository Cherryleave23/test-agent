#!/usr/bin/env python3
# @module admin
"""D3 回归：admin db_status 按 enterprise_id 收敛计数，杜绝跨租户规模泄露。

背景：db_status 原执行全局 COUNT(*) 无 WHERE enterprise_id（server.py:255），
以 ent_X 身份调用会泄露同库 ent_Y / hq 的数据量。本测试同库写入多租户数据，
以 ent_X 身份调 status，断言只返回 ent_X 的规模。

直接运行：python3 test_admin_dbstatus_scoped.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common.config import EnterpriseConfig  # noqa: E402
from kb.store import KnowledgeStore, HQ_ENT  # noqa: E402
from admin.server import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


ENT_X = "ent_X"
ENT_Y = "ent_Y"


def main():
    fails = []
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "shared.db")

    store = KnowledgeStore(db_path, embedding_kind="mock", rerank_kind="none")
    store.add_knowledge(ENT_X, "星飞帆卖点", "小分子好吸收", {"source": "md"})
    store.add_knowledge(ENT_X, "辅食时机", "宝宝辅食添加时机", {"source": "md"})
    store.add_knowledge(ENT_Y, "竞品A卖点", "竞品A 配方", {"source": "md"})
    store.add_knowledge(ENT_Y, "竞品B卖点", "竞品B 配方", {"source": "md"})
    store.add_knowledge(ENT_Y, "竞品C卖点", "竞品C 配方", {"source": "md"})
    store.add_hq_knowledge("新生儿睡眠", "新生儿睡眠周期短", {"vendor": "hq"})
    store.add_hq_knowledge("喂养指南", "按需喂养更安稳", {"vendor": "hq"})

    os.environ.pop("AGENT_ADMIN_TOKEN", None)  # 开发模式放行
    cfg = EnterpriseConfig(enterprise_id=ENT_X, db_path=db_path)
    app = create_app(cfg)
    with TestClient(app) as client:
        r = client.get("/api/database/status")
        if r.status_code != 200:
            fails.append(f"db_status HTTP {r.status_code}")
            print("FAILURES:", fails)
            sys.exit(1)
        body = r.json()
        print(f"[返回] corpus_count={body['corpus_count']} "
              f"products_milk={body['products_milk']} "
              f"products_nutrition={body['products_nutrition']}")

    # ent_X 真实 corpus 仅 2 条
    if body["corpus_count"] != 2:
        fails.append(f"D3: db_status 应仅计 ent_X(2)，实际 {body['corpus_count']}（泄露 ent_Y/hq）")
    else:
        print("[PASS] D3（db_status 已按 enterprise_id 收敛，无跨租户泄露）")

    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL GREEN (db_status 跨租户隔离)")
    sys.exit(0)


if __name__ == "__main__":
    main()
