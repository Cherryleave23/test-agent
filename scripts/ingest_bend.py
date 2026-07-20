#!/usr/bin/env python3
"""B 端商品 markdown 入库工具（知识转化「统一多源接口」的落地 CLI）。

把母婴商品知识库导出的 markdown（含脏数据）解析为 MilkProduct，写入指定企业的
Chroma+SQLite 实例，并可选用真实嵌入（bge-small-zh-v1.5）。

用法（跨平台）：
  # Windows
  python scripts/ingest_bend.py --src %USERPROFILE%\\uploads --enterprise ent_b --db data\\ent_b.db --embedding bge
  # Linux / macOS
  python3 scripts/ingest_bend.py --src ~/uploads --enterprise ent_b --db data/ent_b.db --embedding bge

--src 可为目录（递归 *.md）或若干文件。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from common.config import EmbeddingConfig  # noqa: E402
from kb.store import KnowledgeStore  # noqa: E402
from ingest.markdown_product import ingest_markdown_products  # noqa: E402


def collect_sources(src) -> list:
    p = Path(src)
    if p.is_dir():
        return sorted(p.rglob("*.md"))
    return [p]


def main():
    ap = argparse.ArgumentParser(description="B 端商品 markdown 入库")
    ap.add_argument("--src", required=True, help="商品 markdown 目录或文件")
    ap.add_argument("--enterprise", required=True, help="目标企业 enterprise_id")
    ap.add_argument("--db", required=True, help="实例 SQLite 路径（Chroma 同目录 .chroma）")
    ap.add_argument("--embedding", default="mock", help="mock | bge | bge-small-zh-v1.5")
    ap.add_argument("--rerank", default="none",
                    help="重排器：none（透传）| bge-reranker-v2-m3（开源 cross-encoder，查询期生效）")
    args = ap.parse_args()

    files = collect_sources(args.src)
    if not files:
        print(f"[WARN] 未找到任何 *.md：{args.src}", file=sys.stderr)
        return 1

    cfg = EmbeddingConfig(kind=args.embedding)
    # 重排器在查询期生效（惰性加载），此处仅记录配置，不影响入库行为
    store = KnowledgeStore(args.db, embedding_kind=cfg.kind, rerank_kind=args.rerank)
    ids = ingest_markdown_products(store, [str(f) for f in files], args.enterprise)

    print(f"[OK] 企业 {args.enterprise} 入库 {len(ids)} 个商品 -> {args.db}")
    print(f"     embedding={cfg.kind}  rerank={args.rerank}  chroma={store.chroma_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
