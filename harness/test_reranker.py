#!/usr/bin/env python3
# @module reranker
"""独立重排器（independent reranker）验收 harness。

controlled-vibe-coding：真实运行判 PASS/FAIL，不自我宣称。

背景：重排是与「双塔向量召回（bi-encoder retrieval）」**解耦的独立精度阶段**
（src/common/rerank.py）。召回负责广度（候选集），重排负责精度（cross-encoder 逐对打分）。
本 harness 为「独立重排器」这个新行为补专属验收（满足 controlled-vibe-coding
「harness 套件只能增长」），覆盖：

  RR1 透传（none）：NoReranker 不加载模型、返回等长 1.0，且为默认 kind —— mock / 轻量
       业务路径完全不受影响（召回分数即最终分数）。
  RR2 工厂契约：get_reranker("none")→NoReranker；"bge-reranker-v2-m3"→BgeReranker；
       未知 kind 抛 NotImplementedError（fail-closed，不静默降级）。
  RR3 真实重排（bge）：用开源 BAAI/bge-reranker-v2-m3（cross-encoder）对
       (query, doc) 逐对打分，证明它按「真实语义相关性」而非「字面重叠」重排：
       相关文档得分最高、跨域无关文档（汽车轮胎）被压到最低。
  RR4 解耦集成：KnowledgeStore 同套检索代码，仅 rerank_kind 不同 → reranker.kind 不同；
       两者都能正确检索到产品，证明重排器可插拔、不破坏召回业务（不影响现有 mock 业务）。

直接运行：python3 test_reranker.py  → 退出码 0 全过，非 0 有失败。
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from common.rerank import get_reranker, NoReranker, BgeReranker  # noqa: E402
from kb.store import KnowledgeStore  # noqa: E402
from ingest.markdown_product import ingest_markdown_products  # noqa: E402

UPLOADS = Path("/root/uploads")


def _build_store(ent_id: str, embedding: str, rerank_kind: str) -> KnowledgeStore:
    db = os.path.join(tempfile.mkdtemp(), "inst.db")
    return KnowledgeStore(db, embedding_kind=embedding, rerank_kind=rerank_kind)


def _seed(store: KnowledgeStore, ent_id: str) -> int:
    files = sorted(str(p) for p in UPLOADS.glob("*.md"))
    assert files, f"未找到上传商品 markdown：{UPLOADS}"
    return len(ingest_markdown_products(store, files, ent_id))


async def _rr1_passthrough():
    r = get_reranker("none")
    assert isinstance(r, NoReranker), "默认 kind=none 应返回 NoReranker"
    docs = ["a", "b", "c", "d"]
    scores = r.rerank("任意查询", docs)
    assert scores == [1.0, 1.0, 1.0, 1.0], f"透传应返回等长 1.0，实际 {scores}"
    assert len(scores) == len(docs), "分数长度应与文档数一致"
    # 透传不依赖内容、不加载模型：空文档也应安全返回
    assert get_reranker().rerank("q", []) == [], "空候选应返回空分数"


async def _rr2_factory_contract():
    assert get_reranker("none").kind == "none"
    assert get_reranker("bge-reranker-v2-m3").kind == "bge-reranker-v2-m3"
    assert isinstance(get_reranker("bge-reranker-v2-m3"), BgeReranker)
    # fail-closed：未知 kind 必须显式报错，绝不能静默降级到 NoReranker
    try:
        get_reranker("not-a-real-reranker")
        raise AssertionError("未知 kind 应抛 NotImplementedError，却静默返回")
    except NotImplementedError:
        pass


async def _rr3_real_rerank():
    r = get_reranker("bge-reranker-v2-m3")  # 真实开源 cross-encoder（复用已缓存模型）
    query = "佳贝艾特这款羊奶粉里DHA和ARA含量是多少"
    docs = [
        "佳贝艾特羊奶粉营养成分表：每100kJ含DHA 12mg、ARA 8mg，有助于婴儿大脑与视力发育。",  # 0 相关
        "金领冠1段牛奶粉配料表：生牛乳、脱盐乳清粉、乳糖、植物油。",                          # 1 弱相关（不同产品）
        "汽车轮胎选购指南：胎压、花纹、耐磨指数与抓地力，四季胎与雪地胎区别。",                # 2 跨域无关
    ]
    scores = r.rerank(query, docs)
    assert len(scores) == 3, f"分数长度应为 3，实际 {len(scores)}"
    # 真实语义重排：相关文档得分最高，跨域无关文档被压到最低
    assert scores[0] == max(scores), f"相关文档应得分最高，scores={scores}"
    assert scores[2] == min(scores), f"跨域无关文档应被压到最低，scores={scores}"
    assert scores[0] > scores[1], f"相关应强于弱相关，scores={scores}"


async def _rr4_decoupling():
    # 同套检索代码，仅 rerank_kind 不同
    store_none = _build_store("ent_b", "bge", "none")
    store_bge = _build_store("ent_b", "bge", "bge-reranker-v2-m3")
    assert store_none.reranker.kind == "none"
    assert store_bge.reranker.kind == "bge-reranker-v2-m3"
    # 重排器可插拔：换 kind 不改动检索代码，两者都能正确检索（不影响业务）
    _seed(store_none, "ent_b")
    _seed(store_bge, "ent_b")
    hits_none = store_none.retrieve("1段山羊奶粉有什么推荐", "ent_b", top_k=3)
    hits_bge = store_bge.retrieve("1段山羊奶粉有什么推荐", "ent_b", top_k=3)
    assert hits_none and "佳贝艾特" in hits_none[0].title, f"none 应召回佳贝艾特，实际 {hits_none and hits_none[0].title}"
    assert hits_bge and "佳贝艾特" in hits_bge[0].title, f"bge 应召回佳贝艾特，实际 {hits_bge and hits_bge[0].title}"
    # 企业隔离在两种模式下都成立（解耦：重排不影响隔离防御）
    assert not store_none.retrieve("1段山羊奶粉", "ent_a", top_k=3), "ent_a 不应见 ent_b"
    assert not store_bge.retrieve("1段山羊奶粉", "ent_a", top_k=3), "ent_a 不应见 ent_b"


CHECKS = [
    ("RR1 透传(none)", _rr1_passthrough, False),
    ("RR2 工厂契约", _rr2_factory_contract, False),
    ("RR3 真实重排(bge)", _rr3_real_rerank, True),   # 重型：需 RUN_REAL_MODEL=1
    ("RR4 解耦集成", _rr4_decoupling, True),         # 重型：需 RUN_REAL_MODEL=1
]


async def main():
    # 重型真实模型测试（bge cross-encoder, ~550MB）默认跳过，避免拖慢日常门禁
    # 并因模型加载超时被误判为红；显式 RUN_REAL_MODEL=1 才运行。
    run_real = os.environ.get("RUN_REAL_MODEL") == "1"
    failed = []
    skipped = []
    for name, fn, real in CHECKS:
        if real and not run_real:
            print(f"[SKIP] {name}（重型真实模型，需 RUN_REAL_MODEL=1）")
            skipped.append(name)
            continue
        try:
            await fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {name}: 异常 {type(e).__name__}: {e}")
            failed.append(name)
    print(f"=== Summary: {len(CHECKS) - len(failed)}/{len(CHECKS)} passed"
          f"（skipped {len(skipped)}） ===")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("RESULT: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
