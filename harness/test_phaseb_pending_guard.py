"""Phase B 回归：D8 待确认（未注册）商品合规护栏。

- importer 把 product_text 语料与产品分块打 pending 标记（来自 bundle 的注册号空缺）
- pipeline.answer 在回复点名了 pending 商品时，追加合规提示，不向客户主动推荐
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = "/workspace"
for p in (os.path.join(HERE, "src"), os.path.join(HERE, "tools"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402
from common.config import EnterpriseConfig  # noqa: E402
from kb.store import KnowledgeStore, CorpusHit  # noqa: E402
from agent.pipeline import Agent  # noqa: E402
from dataproc.build import build_bundle  # noqa: E402
from ingest.importer import load_bundle  # noqa: E402


def _make_repo(repo_dir: Path):
    (repo_dir / ".dataproc").mkdir(parents=True, exist_ok=True)
    (repo_dir / ".dataproc" / "repo.json").write_text(json.dumps({
        "name": "t", "enterprise_id": "ent_sim", "namespace": "b",
        "created_at": "2026-07-23T00:00:00+08:00",
    }, ensure_ascii=False), encoding="utf-8")
    (repo_dir / "产品资料").mkdir(parents=True, exist_ok=True)
    # 已注册（confirmed）
    (repo_dir / "产品资料" / "睿护1段.md").write_text(
        "---\nname: 睿护1段\nbrand: 贝贝优\nreg_number: 国食注字YP20180012\nprice: 368\n---\n"
        "# 睿护1段\n贝贝优睿护1段。\n", encoding="utf-8")
    # 未注册（pending：无 reg_number）
    (repo_dir / "产品资料" / "臻羊羊奶粉.md").write_text(
        "---\nname: 臻羊羊奶粉\nbrand: 臻羊\nprice: 398\n---\n"
        "# 臻羊羊奶粉\n臻羊1段羊奶粉。\n", encoding="utf-8")


def test_importer_tags_pending_in_meta():
    tmp = Path(tempfile.mkdtemp(prefix="pb_imp_"))
    repo = tmp / "repo"
    _make_repo(repo)
    bundle = tmp / "bundle"
    build_bundle(str(repo), str(bundle))

    db = str(tmp / "instance.db")
    store = KnowledgeStore(db, embedding_kind="mock")
    load_bundle(str(bundle), store, "ent_sim")

    # 待确认商品存在
    pending = store.list_pending_products("ent_sim")
    assert any(p["name"] == "臻羊羊奶粉" for p in pending), pending

    con = __import__("sqlite3").connect(db)
    # product_text 语料应带 pending 标记
    rows = con.execute(
        "SELECT meta_json FROM corpus WHERE enterprise_id='ent_sim' "
        "AND json_extract(meta_json,'$.kind')='product_text'").fetchall()
    assert rows, "应有 product_text 语料"
    assert any(json.loads(r[0]).get("pending") for r in rows), [json.loads(r[0]) for r in rows]

    # 产品分块（b_milk）应带 pending 标记
    rows2 = con.execute(
        "SELECT meta_json FROM corpus WHERE enterprise_id='ent_sim' "
        "AND part='b_milk'").fetchall()
    assert any(json.loads(r[0]).get("pending") for r in rows2), "b_milk 分块应带 pending"


def test_agent_cautions_on_pending_product():
    cfg = EnterpriseConfig(enterprise_id="ent_sim", llm={"kind": "mock"},
                           embedding={"kind": "mock"})
    tmp = Path(tempfile.mkdtemp(prefix="pb_ag_"))
    store = KnowledgeStore(str(tmp / "instance.db"), embedding_kind="mock")
    agent = Agent(cfg, store)

    pending_hit = CorpusHit(
        id=1, part="b_milk", enterprise_id="ent_sim",
        title="臻羊羊奶粉", content="臻羊1段羊奶粉特点",
        meta={"name": "臻羊羊奶粉", "pending": True}, score=0.0)

    async def run():
        agent.store.retrieve = lambda *a, **k: [pending_hit]
        ans = await agent.answer("臻羊羊奶粉多少钱")
        return ans.text
    text = asyncio.run(run())
    assert "臻羊羊奶粉" in text
    assert "合规提示" in text and "注册号待确认" in text


def test_agent_no_caution_on_confirmed_product():
    cfg = EnterpriseConfig(enterprise_id="ent_sim", llm={"kind": "mock"},
                           embedding={"kind": "mock"})
    tmp = Path(tempfile.mkdtemp(prefix="pb_ok_"))
    store = KnowledgeStore(str(tmp / "instance.db"), embedding_kind="mock")
    agent = Agent(cfg, store)

    confirmed_hit = CorpusHit(
        id=2, part="b_milk", enterprise_id="ent_sim",
        title="睿护1段", content="睿护1段特点",
        meta={"name": "睿护1段", "pending": False}, score=0.0)

    async def run():
        agent.store.retrieve = lambda *a, **k: [confirmed_hit]
        ans = await agent.answer("睿护1段多少钱")
        return ans.text
    text = asyncio.run(run())
    assert "睿护1段" in text
    assert "合规提示" not in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
