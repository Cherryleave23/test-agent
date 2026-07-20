"""知识库存储（三库模型，O1/D2 → Chroma）。

- 向量检索：**Chroma 嵌入式 PersistentClient**（每实例一个持久化目录 = 物理企业隔离；
  原生 metadata 过滤按 enterprise_id 强化隔离；HQ 共享库以 enterprise_id='hq'）。
- 结构化产品表 + 会话 + 关键词索引：**SQLite**（同实例库）。
- HQ 知识库（共享，随分发）：corpus.part='hq_kb'，enterprise_id IS NULL（Chroma 中 'hq'）。
- B-end 结构化产品：products_milk / products_nutrition（每企业隔离）。
- 统一检索语料 corpus(FTS5) + Chroma 向量，RRF 混合检索。
- HQ 商品库（厂商侧复用）：hq_products，onboarding 播种用。
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from dataclasses import dataclass
from typing import List, Optional

import chromadb

from common.db import connect
from common.embeddings import embed, DIM, _domain_tokens
from common.rerank import get_reranker
from kb.models import MilkProduct, NutritionProduct

HQ_ENT = "hq"  # Chroma metadata 中 HQ 共享库的 enterprise_id 标记


def _fts_tokenize(text: str) -> List[str]:
    """FTS5 索引 / 查询统一分词：英文数字词 + 命中的母婴复合词（不含单字）。

    unicode61 按空白切分，故复合词各自成为独立 token，CJK 可整体命中；
    不做单字切分，避免「卖」等通用字在跨域查询与「卖点」块间误匹配（H4/R4 根因）。
    """
    return _domain_tokens(text)


@dataclass
class CorpusHit:
    id: int
    part: str
    enterprise_id: Optional[str]
    title: str
    content: str
    meta: dict
    score: float
    product_id: Optional[int] = None  # 所属结构化产品主键（HQ/非产品为 None）
    chunk: Optional[str] = None        # 命中的语义分块标签（基础信息/配料表/营养成分/…）


def _chroma_path(db_path: str) -> str:
    return str(db_path).rsplit(".", 1)[0] + ".chroma"


class KnowledgeStore:
    def __init__(self, db_path: str, embedding_kind: str = "mock",
                 chroma_path: Optional[str] = None, rerank_kind: str = "none"):
        self.db_path = db_path
        self.embedding_kind = embedding_kind
        self.chroma_path = chroma_path or _chroma_path(db_path)
        self.reranker = get_reranker(rerank_kind)  # 独立重排器（mock 默认透传，不加载模型）
        os.makedirs(self.chroma_path, exist_ok=True)
        self.chroma = chromadb.PersistentClient(path=self.chroma_path)
        self.collection = self.chroma.get_or_create_collection(
            "corpus", metadata={"hnsw:space": "l2"}
        )
        self._init_schema()

    # ---------- schema ----------
    def _init_schema(self) -> None:
        with connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS corpus (
                    id INTEGER PRIMARY KEY,
                    part TEXT NOT NULL,
                    enterprise_id TEXT,
                    title TEXT,
                    content TEXT,
                    meta_json TEXT,
                    product_id INTEGER,
                    chunk TEXT
                )
                """
            )
            # 兼容已有实例库：缺失列则补（迁移）。
            for col, typ in (("product_id", "INTEGER"), ("chunk", "TEXT")):
                try:
                    cur.execute(f"ALTER TABLE corpus ADD COLUMN {col} {typ}")
                except sqlite3.OperationalError:
                    pass  # 已存在
            cur.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts_corpus "
                "USING fts5(title, content, content='corpus', content_rowid='id')"
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS products_milk (
                    id INTEGER PRIMARY KEY, enterprise_id TEXT NOT NULL,
                    name TEXT, brand TEXT, stage TEXT, age_range TEXT, price REAL,
                    origin TEXT, milk_origin TEXT, ptype TEXT, reg_number TEXT,
                    manufacturer TEXT, ingredients TEXT, nutrition TEXT, highlights TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS products_nutrition (
                    id INTEGER PRIMARY KEY, enterprise_id TEXT NOT NULL,
                    name TEXT, brand TEXT, category TEXT, audience TEXT, dosage_form TEXT,
                    age_range TEXT, price REAL, origin TEXT, manufacturer TEXT,
                    health_license TEXT, efficacy TEXT, ingredients TEXT, nutrition TEXT,
                    highlights TEXT, cautions TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS hq_products (
                    id INTEGER PRIMARY KEY, kind TEXT, brand TEXT, name TEXT,
                    reg_number TEXT, meta_json TEXT
                )
                """
            )
            conn.commit()

    # ---------- 写：HQ 知识库 ----------
    def add_hq_knowledge(self, title: str, content: str) -> int:
        with connect(self.db_path) as conn:
            cur = conn.cursor()
            cid = self._add_corpus(
                cur, "hq_kb", HQ_ENT, None, title, content,
                {"kind": "hq_kb"},
            )
            conn.commit()
            return cid

    @staticmethod
    def fts_text(title: str, content: str) -> str:
        """可检索文本：拆解为「中文单字 + 英文/数字词 + 母婴复合词」空格分隔 token。

        FTS5 默认 unicode61 分词器不按字切分 CJK（整段中文被视为一个 token），
        因此显式将内容拆成单字与复合词 token；查询端用同一逻辑，才能做字级重叠召回。
        """
        return " ".join(_fts_tokenize(title + " " + content))

    # ---------- 写：B-end 产品（语义分块入库）----------
    def add_milk(self, p: MilkProduct) -> int:
        with connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO products_milk(enterprise_id,name,brand,stage,age_range,price,
                   origin,milk_origin,ptype,reg_number,manufacturer,ingredients,nutrition,highlights)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (p.enterprise_id, p.name, p.brand, p.stage, p.age_range, p.price,
                 p.origin, p.milk_origin, p.ptype, p.reg_number, p.manufacturer,
                 p.ingredients, p.nutrition, p.highlights),
            )
            pid = cur.lastrowid
            for idx, (label, text) in enumerate(p.to_chunks()):
                self._add_corpus(cur, "b_milk", p.enterprise_id, pid, p.name, text,
                                 p.meta(), chunk=label, chunk_index=idx)
            conn.commit()
            return pid

    def add_nutrition(self, p: NutritionProduct) -> int:
        with connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO products_nutrition(enterprise_id,name,brand,category,audience,
                   dosage_form,age_range,price,origin,manufacturer,health_license,efficacy,
                   ingredients,nutrition,highlights,cautions)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (p.enterprise_id, p.name, p.brand, p.category, p.audience, p.dosage_form,
                 p.age_range, p.price, p.origin, p.manufacturer, p.health_license, p.efficacy,
                 p.ingredients, p.nutrition, p.highlights, p.cautions),
            )
            pid = cur.lastrowid
            for idx, (label, text) in enumerate(p.to_chunks()):
                self._add_corpus(cur, "b_nutrition", p.enterprise_id, pid, p.name, text,
                                 p.meta(), chunk=label, chunk_index=idx)
            conn.commit()
            return pid

    def _add_corpus(self, cur, part, ent, product_id, title, text, meta,
                    chunk: str = "", chunk_index: int = 0) -> int:
        cur.execute(
            "INSERT INTO corpus(part, enterprise_id, title, content, meta_json, product_id, chunk) "
            "VALUES(?,?,?,?,?,?,?)",
            (part, ent, title, text, json.dumps(meta, ensure_ascii=False),
             product_id, chunk),
        )
        cid = cur.lastrowid
        self._index(cur, cid, title, text, ent, part, product_id=product_id, chunk=chunk)
        return cid

    def _index(self, cur, cid: int, title: str, content: str, ent: str, part: str,
               product_id=None, chunk: str = "") -> None:
        vec = embed(title + " " + content, self.embedding_kind)
        # Chroma 向量（metadata 过滤用 enterprise_id；product_id 供结构化预过滤 $in）
        pid_meta = product_id if product_id is not None else -1
        self.collection.upsert(
            ids=[str(cid)],
            embeddings=[vec],
            documents=[content],
            metadatas=[{"enterprise_id": ent, "part": part,
                        "product_id": pid_meta, "chunk": chunk}],
        )
        # SQLite FTS5 关键词索引（字级 token 化，CJK 可命中）
        cur.execute(
            "INSERT INTO fts_corpus(rowid, title, content) VALUES(?,?,?)",
            (cid, title, self.fts_text(title, content)),
        )

    # ---------- 读：混合检索（Chroma 向量 + FTS5 关键词，RRF）----------
    # 相关性阈值门控：向量相似度低于阈值视为「无相关信息」，防止跨域幻觉（RAG 标准做法）。
    # mock 词袋零重叠趋近正交（L2≈√2≈1.4142），门控取 1.405（<√2）干净丢弃跨域；
    # 在域查询因共享词袋 L2 明显更小（<1.4），得以保留（关键词召回另由 FTS5 兜底）。
    RELEVANCE_MAX_DIST = 1.405

    # 结构化产品表列（用于结构化预过滤的合法 filter key 校验）
    _MILK_COLS = {"name", "brand", "stage", "age_range", "price", "origin",
                  "milk_origin", "ptype", "reg_number", "manufacturer"}
    _NUT_COLS = {"name", "brand", "category", "audience", "dosage_form", "age_range",
                 "price", "origin", "manufacturer", "health_license", "efficacy"}

    def _filter_product_ids(self, enterprise_id: str, filters: dict) -> List[int]:
        """结构化预过滤：在结构化产品表里圈定满足 filters 的产品主键集合。

        仅查询「包含 filters 全部 key 作为列」的表（奶粉/营养品分别校验），
        返回匹配的产品 id 列表（供 Chroma $in 与 FTS JOIN 限制候选集）。
        """
        ids: List[int] = []
        tables = [("products_milk", self._MILK_COLS), ("products_nutrition", self._NUT_COLS)]
        for tbl, cols in tables:
            if not all(k in cols for k in filters):
                continue
            conds = ["enterprise_id=?"]
            params: List = [enterprise_id]
            for k, v in filters.items():
                conds.append(f"{k}=?")
                params.append(v)
            with connect(self.db_path) as conn:
                for r in conn.execute(
                    f"SELECT id FROM {tbl} WHERE {' AND '.join(conds)}", params
                ).fetchall():
                    ids.append(r["id"])
        return ids

    def retrieve(self, query: str, enterprise_id: str, top_k: int = 5,
                 filters: Optional[dict] = None) -> List[CorpusHit]:
        qvec = embed(query, self.embedding_kind)

        # 结构化预过滤：先把候选产品集圈定（filters=None 表示不限）
        allowed_ids = self._filter_product_ids(enterprise_id, filters) if filters else None

        # 1) Chroma 向量召回（metadata 过滤：本企业 + HQ 共享；filters 时再限 product_id）
        chroma_ids: List[tuple] = []
        where = {"$or": [{"enterprise_id": enterprise_id}, {"enterprise_id": HQ_ENT}]}
        if filters and allowed_ids is not None:
            where = {"$and": [where, {"product_id": {"$in": allowed_ids}}]}
        try:
            res = self.collection.query(
                query_embeddings=[qvec],
                n_results=top_k * 8,
                where=where,
            )
            ids = res["ids"][0] if res.get("ids") else []
            dists = res["distances"][0] if res.get("distances") else []
            # 相关性阈值门控：向量相似度低于阈值视为「无相关信息」，防止跨域幻觉。
            # mock 词袋（正交≈√2≈1.414）用 1.45；bge 归一化向量实测分布：
            # 在域查询 L2≈0.81~0.87，跨域查询 L2≈1.17+，故取 1.05 干净分离。
            max_dist = 1.05 if self.embedding_kind.startswith("bge") else self.RELEVANCE_MAX_DIST
            chroma_ids = [
                (int(i), math.sqrt(d)) for i, d in zip(ids, dists)
                if math.sqrt(d) <= max_dist
            ]
        except Exception:
            chroma_ids = []

        # 2) SQLite FTS5 关键词召回（关键词命中即相关，不受阈值限制）
        #    filters 时 JOIN corpus 仅取限定 product_id 的分块。
        fts_ids: List[tuple] = []
        try:
            with connect(self.db_path) as conn:
                if filters and allowed_ids is not None:
                    ph = ",".join("?" * len(allowed_ids)) if allowed_ids else "NULL"
                    sql = (
                        "SELECT f.rowid, f.rank FROM fts_corpus f "
                        "JOIN corpus c ON c.id=f.rowid "
                        f"WHERE f.fts_corpus MATCH ? AND c.product_id IN ({ph}) "
                        "ORDER BY f.rank LIMIT ?"
                    )
                    params = [self._fts_query(query)] + list(allowed_ids) + [top_k * 8]
                else:
                    sql = ("SELECT rowid, rank FROM fts_corpus WHERE fts_corpus MATCH ? "
                           "ORDER BY rank LIMIT ?")
                    params = [self._fts_query(query), top_k * 8]
                rows = conn.execute(sql, params).fetchall()
            fts_ids = [(r["rowid"], r["rank"]) for r in rows]
        except sqlite3.OperationalError:
            fts_ids = []

        # 3) RRF 融合
        fused = self._rrf(chroma_ids, fts_ids, k=60)

        # 4) 回查 corpus，收集候选分块（召回阶段只负责「广度」）
        PRODUCT_BOOST = 1.25  # 产品块略加权（零售问答主诉求；HQ 为补充背景）
        chunks: list = []
        with connect(self.db_path) as conn:
            for cid, score in fused:
                row = conn.execute(
                    "SELECT id, part, enterprise_id, title, content, meta_json, "
                    "product_id, chunk FROM corpus WHERE id=?",
                    (cid,),
                ).fetchone()
                if not row:
                    continue
                if row["enterprise_id"] is not None and row["enterprise_id"] != enterprise_id:
                    continue
                meta = json.loads(row["meta_json"] or "{}")
                # 结构化过滤（防御纵深）：meta 中字段需与 filters 完全匹配
                if filters and not all(meta.get(k) == v for k, v in filters.items()):
                    continue
                pid = row["product_id"]
                key = (row["part"], pid) if pid is not None else ("row", row["id"])
                chunks.append({
                    "key": key, "recall": score,
                    "title": row["title"], "row_id": row["id"], "pid": pid,
                    "part": row["part"], "ent": row["enterprise_id"], "meta": meta,
                    "content": row["content"], "chunk": row["chunk"] or "",
                })
        # 5) 独立重排阶段（rerank）：cross-encoder 对 (query, 分块) 逐对重打分。
        #    - reranker=none（mock/轻量）：召回分数合并到产品，取最佳召回块为代表，
        #      不加载模型、不影响业务；
        #    - 真实 reranker（bge-reranker-v2-m3）：在「分块粒度」重排，再按产品归并、
        #      取各产品重排分最高的块为引用代表——既保证「一个产品一个答案」，又保证
        #      引用块是查询最相关的那块（分块精度：DHA→营养成分、奶基→配料表）。
        use_rerank = self.reranker.kind != "none"
        if use_rerank and chunks:
            docs = [c["content"] for c in chunks]
            r_scores = self.reranker.rerank(query, docs)
            if os.environ.get("RERANK_DEBUG"):
                for c, rs in zip(chunks, r_scores):
                    print(f"[RERANK] {c['title']} | {c['chunk']} | {rs:.4f} | {c['content'][:30]}")
            for c, rs in zip(chunks, r_scores):
                c["final"] = float(rs)
        groups: dict = {}
        for c in chunks:
            g = groups.get(c["key"])
            if g is None:
                g = {"title": c["title"], "row_id": c["row_id"], "pid": c["pid"],
                     "part": c["part"], "ent": c["ent"], "meta": c["meta"],
                     "best_score": 0.0, "best_text": "", "best_chunk": ""}
                groups[c["key"]] = g
            cand = c["final"] if use_rerank else c["recall"]
            if cand > g["best_score"]:
                g["best_score"] = cand
                g["best_text"] = c["content"]
                g["best_chunk"] = c["chunk"]
        scored = []
        for g in groups.values():
            final = g["best_score"] * (PRODUCT_BOOST if g["part"] in ("b_milk", "b_nutrition") else 1.0)
            scored.append((final, g))
        scored.sort(key=lambda x: -x[0])
        hits: List[CorpusHit] = []
        for s, g in scored[:top_k]:
            hits.append(CorpusHit(
                id=g["pid"] if g["pid"] is not None else g["row_id"],
                part=g["part"], enterprise_id=g["ent"], title=g["title"],
                content=g["best_text"], meta=g["meta"], score=s,
                product_id=g["pid"], chunk=g["best_chunk"],
            ))
        return hits

    @staticmethod
    def _fts_query(text: str) -> str:
        # 与索引端同一套领域分词：复合词 OR 连接；无领域 token（OOV）则不匹配任何文档。
        toks = _fts_tokenize(text)
        if not toks:
            return "__OOV__"
        return " OR ".join(toks)

    @staticmethod
    def _rrf(vec_ids, fts_ids, k: int = 60):
        score = {}
        for rank, (cid, _) in enumerate(vec_ids):
            score[cid] = score.get(cid, 0.0) + 1.0 / (k + rank + 1)
        for rank, (cid, _) in enumerate(fts_ids):
            score[cid] = score.get(cid, 0.0) + 1.0 / (k + rank + 1)
        return sorted(score.items(), key=lambda x: -x[1])
