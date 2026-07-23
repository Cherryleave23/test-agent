#!/usr/bin/env python3
"""真实部署探针 P2：admin db_status 是否跨租户计数泄露（Stage2 部署隔离）。

模拟「B端部署模型」：同一 DB 被多租户数据共存（端侧共享库 / 服务商托管多企业），
以 ent_X 身份调 GET /api/database/status，看是否泄露 ent_Y / hq 的规模。

运行：python3 harness/probe_dbstatus_leak.py
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
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "shared.db")

    # 1) 同库写入三租户数据（模拟共享 DB：ent_X 本企 + ent_Y 他企 + hq 共享库）
    store = KnowledgeStore(db_path, embedding_kind="mock", rerank_kind="none")
    store.add_knowledge(ENT_X, "星飞帆卖点", "小分子好吸收", {"source": "md"})
    store.add_knowledge(ENT_X, "辅食时机", "宝宝辅食添加时机", {"source": "md"})
    store.add_knowledge(ENT_Y, "竞品A卖点", "竞品A 配方", {"source": "md"})  # 他企数据
    store.add_knowledge(ENT_Y, "竞品B卖点", "竞品B 配方", {"source": "md"})
    store.add_knowledge(ENT_Y, "竞品C卖点", "竞品C 配方", {"source": "md"})
    store.add_hq_knowledge("新生儿睡眠", "新生儿睡眠周期短", {"vendor": "hq"})  # HQ 共享
    store.add_hq_knowledge("喂养指南", "按需喂养更安稳", {"vendor": "hq"})

    # 2) 以 ent_X 身份启动 admin（开发模式：不设 AGENT_ADMIN_TOKEN 则放行）
    os.environ.pop("AGENT_ADMIN_TOKEN", None)
    cfg = EnterpriseConfig(enterprise_id=ENT_X, db_path=db_path)
    app = create_app(cfg)

    with TestClient(app) as client:
        r = client.get("/api/database/status")
        print(f"[HTTP] {r.status_code}")
        body = r.json()
        print(f"[返回] corpus_count={body['corpus_count']} "
              f"products_milk={body['products_milk']} "
              f"products_nutrition={body['products_nutrition']}")

    # 3) 真实分布（按租户核账）
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        dist = {}
        for r in conn.execute("SELECT enterprise_id, COUNT(*) c FROM corpus GROUP BY enterprise_id"):
            dist[r["enterprise_id"]] = r["c"]
    print(f"\n[真实分布] {dict(dist)}  →  ent_X 应有 2 条")

    print("\n" + "=" * 50)
    # ent_X 真实 corpus 仅 2 条，但 db_status 返回 7（含 ent_Y 的 3 + hq 的 2）
    x_only = dist.get(ENT_X, 0)
    if body["corpus_count"] != x_only:
        print(f"❌ 实证结论：db_status 返回 {body['corpus_count']} 条，"
              f"而 ent_X 真实仅有 {x_only} 条 → 泄露了 ent_Y({dist.get(ENT_Y,0)}) "
              f"与 hq({dist.get(HQ_ENT,0)}) 的规模（全局 COUNT 无 enterprise_id 过滤）。")
        sys.exit(1)
    else:
        print("✅ db_status 已按 enterprise_id 收敛。")
        sys.exit(0)


if __name__ == "__main__":
    main()
