"""知识库存储（三库模型，O1/D2 → Chroma）。

- 向量检索：**Chroma 嵌入式 PersistentClient**（每实例一个持久化目录 = 物理企业隔离；
  原生 metadata 过滤按 enterprise_id 强化隔离；HQ 共享库以 enterprise_id='hq'）。
- 结构化产品表 + 会话 + 关键词索引：**SQLite**（同实例库）。
- HQ 知识库（共享，随分发）：corpus.part='hq_kb'，enterprise_id='hq'（SQLite 与 Chroma 一致，见 HQ_ENT）。
- B-end 结构化产品：products_milk / products_nutrition（每企业隔离）。
- 统一检索语料 corpus(FTS5) + Chroma 向量，RRF 混合检索。
- HQ 商品库（厂商侧复用）：hq_products，onboarding 播种用。
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import logging
from dataclasses import dataclass
from typing import List, Optional

import chromadb

from common.db import connect, db_tx
from common.embeddings import embed, DIM, _domain_tokens
from common.rerank import get_reranker
from kb.models import MilkProduct, NutritionProduct

logger = logging.getLogger(__name__)

HQ_ENT = "hq"  # Chroma metadata 中 HQ 共享库的 enterprise_id 标记


class ReadonlyError(Exception):
    """试图删除/改写 HQ（厂商分发只读）语料时抛出。"""


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
        with db_tx(self.db_path) as conn:
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
            # 采集去重表：跨运行内容哈希去重（相同 source_type+内容不重复入库）
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ingest_dedup (
                    enterprise_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    chash TEXT NOT NULL,
                    PRIMARY KEY (enterprise_id, source_type, chash)
                )
                """
            )
            conn.commit()

    # ---------- 写：HQ 知识库 ----------
    def add_hq_knowledge(self, title: str, content: str,
                         meta: Optional[dict] = None) -> int:
        # 分区已由 corpus.part='hq_kb' 承担，meta 不再冗余写 kind='hq_kb'，
        # 改由调用方写入内容类型 kind（article/ingredient 等），避免与新契约 kind 语义撞车（F1）。
        # F2：HQ 为厂商分发共享库，实例侧只读 —— 只读性由分区（ent='hq'）保证，
        # delete_corpus/update_corpus 经 _row_readonly 拒绝改写（不污染内容型 meta）。
        hq_meta = dict(meta or {})
        with db_tx(self.db_path) as conn:
            cur = conn.cursor()
            cid = self._add_corpus(
                cur, "hq_kb", HQ_ENT, None, title, content, hq_meta,
            )
            conn.commit()
            return cid

    # ---------- 写：HQ 商品库（厂商侧复用，onboarding 播种）----------
    def add_hq_product(self, fields: dict, meta: Optional[dict] = None) -> int:
        with db_tx(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO hq_products(kind, brand, name, reg_number, meta_json) "
                "VALUES(?,?,?,?,?)",
                (fields.get("kind"), fields.get("brand"), fields.get("name"),
                 fields.get("reg_number"),
                 json.dumps(meta or {}, ensure_ascii=False)),
            )
            pid = cur.lastrowid
            conn.commit()
            return pid

    def get_hq_products(self) -> List[dict]:
        with db_tx(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, kind, brand, name, reg_number, meta_json "
                "FROM hq_products"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 读/写：待确认商品（F5 数据侧基础）----------
    # pending 判定列：奶粉用注册号 reg_number，营养品用批准文号 health_license
    _PENDING_COL = {"products_milk": "reg_number", "products_nutrition": "health_license"}

    def list_pending_products(self, enterprise_id: str) -> List[dict]:
        """列出本企业「待确认」商品（奶粉 reg_number 空 / 营养品 health_license 空）。

        供微信侧「待确认列表」展示；确认后写对应批准号即不再 pending。
        """
        out: List[dict] = []
        # P2-22: 单连接遍历两张表，避免重复开关连接
        with db_tx(self.db_path) as conn:
            for tbl, col in self._PENDING_COL.items():
                for r in conn.execute(
                    f"SELECT id, name, brand, {col} FROM {tbl} "
                    f"WHERE enterprise_id=? AND ({col} IS NULL OR {col}='')",
                    (enterprise_id,),
                ).fetchall():
                    d = dict(r)
                    d["table"] = tbl
                    d["pending_key"] = col
                    out.append(d)
        return out

    def confirm_product(self, product_id: int, value: str,
                        table: str = "products_milk",
                        enterprise_id: Optional[str] = None) -> None:
        """确认 pending 商品：写入对应批准号（奶粉 reg_number / 营养品 health_license）。

        P0-04: 当传入 enterprise_id 时，校验商品归属，防跨租户越权。
        """
        if table not in self._PENDING_COL:
            raise ValueError(f"未知商品表: {table}")
        col = self._PENDING_COL[table]
        with db_tx(self.db_path) as conn:
            if enterprise_id is not None:
                row = conn.execute(
                    f"SELECT enterprise_id FROM {table} WHERE id=?", (product_id,)
                ).fetchone()
                if row is None:
                    raise ValueError(f"商品不存在: id={product_id}")
                if row["enterprise_id"] != enterprise_id:
                    raise PermissionError(
                        f"跨租户越权: 商品 id={product_id} 不属于 enterprise_id={enterprise_id}"
                    )
            conn.execute(
                f"UPDATE {table} SET {col}=? WHERE id=?",
                (value, product_id),
            )
            conn.commit()

    def delete_product(self, product_id: int, table: str = "products_milk",
                       enterprise_id: Optional[str] = None) -> None:
        """删除商品及其绑定的语料分块（b_milk/b_nutrition，按 product_id）。

        P0-04: 当传入 enterprise_id 时，校验商品归属，防跨租户越权。
        P2-24: Chroma 批量删除，避免逐条调用。
        """
        if table not in ("products_milk", "products_nutrition"):
            raise ValueError(f"未知商品表: {table}")
        with db_tx(self.db_path) as conn:
            cur = conn.cursor()
            # P0-04: 跨租户校验
            if enterprise_id is not None:
                row = cur.execute(
                    f"SELECT enterprise_id FROM {table} WHERE id=?", (product_id,)
                ).fetchone()
                if row is None:
                    raise ValueError(f"商品不存在: id={product_id}")
                if row["enterprise_id"] != enterprise_id:
                    raise PermissionError(
                        f"跨租户越权: 商品 id={product_id} 不属于 enterprise_id={enterprise_id}"
                    )
            ids = [r["id"] for r in cur.execute(
                "SELECT id FROM corpus WHERE product_id=?", (product_id,)).fetchall()]
            if ids:
                ph = ",".join("?" * len(ids))
                cur.execute(f"DELETE FROM fts_corpus WHERE rowid IN ({ph})", ids)
            cur.execute("DELETE FROM corpus WHERE product_id=?", (product_id,))
            cur.execute(f"DELETE FROM {table} WHERE id=?", (product_id,))
            conn.commit()
        # P2-24: 批量删除 Chroma 向量
        if ids:
            try:
                self.collection.delete(ids=[str(i) for i in ids])
            except Exception as e:
                # P2-N7: 记录警告而非静默忽略，便于排查孤儿向量
                logger.warning("Chroma 向量删除失败 (corpus ids=%s): %s: %s", ids, type(e).__name__, e)

    # ---------- 写：企业自有 RAG 知识（采集归一后的文本源：web/text/pdf/...）----------
    def add_knowledge(self, enterprise_id: str, title: str, content: str,
                     meta: Optional[dict] = None, product_id: Optional[int] = None) -> int:
        # product_id: 绑定具体商品的结构化主键（corpus 中 product_text/ingredient 用），
        # 供 importer 把 bundle 的 product_uid 解析为 product_id（F1）。
        with db_tx(self.db_path) as conn:
            cur = conn.cursor()
            cid = self._add_corpus(
                cur, "b_kb", enterprise_id, product_id, title, content,
                meta or {},
            )
            conn.commit()
            return cid

    # ---------- 采集去重（跨运行内容哈希）----------
    def is_ingested(self, enterprise_id: str, source_type: str, chash: str) -> bool:
        with db_tx(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM ingest_dedup WHERE enterprise_id=? AND source_type=? AND chash=?",
                (enterprise_id, source_type, chash),
            ).fetchone()
            return row is not None

    def mark_ingested(self, enterprise_id: str, source_type: str, chash: str) -> None:
        with db_tx(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO ingest_dedup(enterprise_id, source_type, chash) VALUES(?,?,?)",
                (enterprise_id, source_type, chash),
            )
            conn.commit()

    @staticmethod
    def fts_text(title: str, content: str) -> str:
        """可检索文本：拆解为「中文单字 + 英文/数字词 + 母婴复合词」空格分隔 token。

        FTS5 默认 unicode61 分词器不按字切分 CJK（整段中文被视为一个 token），
        因此显式将内容拆成单字与复合词 token；查询端用同一逻辑，才能做字级重叠召回。
        """
        return " ".join(_fts_tokenize(title + " " + content))

    # ---------- 写：B-end 产品（语义分块入库）----------
    def add_milk(self, p: MilkProduct) -> int:
        with db_tx(self.db_path) as conn:
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
        with db_tx(self.db_path) as conn:
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

    # ---------- 写：HQ 只读护栏（F2）----------
    @staticmethod
    def _row_readonly(row) -> bool:
        """判定某 corpus 行是否只读：HQ 共享库（ent=hq）或 meta.readonly=true。"""
        if row["enterprise_id"] == HQ_ENT:
            return True
        try:
            return bool(json.loads(row["meta_json"] or "{}").get("readonly"))
        except Exception:
            return False

    def delete_corpus(self, cid: int) -> None:
        """删除一条语料。HQ（厂商分发只读）行拒绝删除（F2）。"""
        with db_tx(self.db_path) as conn:
            cur = conn.cursor()
            row = cur.execute(
                "SELECT enterprise_id, meta_json FROM corpus WHERE id=?", (cid,)
            ).fetchone()
            if row is None:
                return
            if self._row_readonly(row):
                raise ReadonlyError(
                    "HQ 知识库为厂商分发只读，实例不可删除（enterprise_id=hq 或 meta.readonly=true）"
                )
            cur.execute("DELETE FROM fts_corpus WHERE rowid=?", (cid,))
            cur.execute("DELETE FROM corpus WHERE id=?", (cid,))
            conn.commit()
        try:
            self.collection.delete(ids=[str(cid)])
        except Exception:
            pass  # Chroma 缺该 id 不阻塞

    def update_corpus(self, cid: int, title: Optional[str] = None,
                      content: Optional[str] = None,
                      meta: Optional[dict] = None) -> None:
        """改写一条语料。HQ（厂商分发只读）行拒绝改写（F2）。

        厂商重分发仍走 add_hq_knowledge（vendor 路径），不受本护栏限制。
        """
        with db_tx(self.db_path) as conn:
            cur = conn.cursor()
            row = cur.execute(
                "SELECT enterprise_id, meta_json, part, product_id, chunk, title, content "
                "FROM corpus WHERE id=?", (cid,)
            ).fetchone()
            if row is None:
                return
            if self._row_readonly(row):
                raise ReadonlyError(
                    "HQ 知识库为厂商分发只读，实例不可改写（enterprise_id=hq 或 meta.readonly=true）"
                )
            new_title = title if title is not None else row["title"]
            new_content = content if content is not None else row["content"]
            new_meta = row["meta_json"]
            if meta is not None:
                m = json.loads(row["meta_json"] or "{}")
                m.update(meta)
                new_meta = json.dumps(m, ensure_ascii=False)
            cur.execute(
                "UPDATE corpus SET title=?, content=?, meta_json=? WHERE id=?",
                (new_title, new_content, new_meta, cid),
            )
            cur.execute("DELETE FROM fts_corpus WHERE rowid=?", (cid,))
            conn.commit()
            # P2-25: 在连接关闭前提取 Row 值，避免连接关闭后访问
            row_ent = row["enterprise_id"]
            row_part = row["part"]
            row_pid = row["product_id"]
            row_chunk = row["chunk"] or ""
        # 重新索引（chroma upsert + fts 插入）；在连接外调用 chroma 安全
        upd_kind = (json.loads(new_meta).get("kind", "") if new_meta else "")
        self._index(
            None, cid, new_title, new_content, row_ent, row_part,
            product_id=row_pid, chunk=row_chunk, kind=upd_kind,
        )

    def _add_corpus(self, cur, part, ent, product_id, title, text, meta,
                    chunk: str = "", chunk_index: int = 0) -> int:
        cur.execute(
            "INSERT INTO corpus(part, enterprise_id, title, content, meta_json, product_id, chunk) "
            "VALUES(?,?,?,?,?,?,?)",
            (part, ent, title, text, json.dumps(meta, ensure_ascii=False),
             product_id, chunk),
        )
        cid = cur.lastrowid
        self._index(cur, cid, title, text, ent, part, product_id=product_id,
                    chunk=chunk, kind=(meta or {}).get("kind", ""))
        return cid

    def _index(self, cur, cid: int, title: str, content: str, ent: str, part: str,
               product_id=None, chunk: str = "", kind: str = "") -> None:
        vec = embed(title + " " + content, self.embedding_kind)
        # Chroma 向量（metadata 过滤用 enterprise_id；product_id 供结构化预过滤 $in；
        # kind 供检索侧按内容类型路由/加权，F3）
        pid_meta = product_id if product_id is not None else -1
        self.collection.upsert(
            ids=[str(cid)],
            embeddings=[vec],
            documents=[content],
            metadatas=[{"enterprise_id": ent, "part": part,
                        "product_id": pid_meta, "chunk": chunk, "kind": kind}],
        )
        # SQLite FTS5 关键词索引（字级 token 化，CJK 可命中）
        # cur=None 时（如 update_corpus 在连接外重索引）自建连接写入
        if cur is None:
            with db_tx(self.db_path) as _c:
                _c.execute(
                    "INSERT INTO fts_corpus(rowid, title, content) VALUES(?,?,?)",
                    (cid, title, self.fts_text(title, content)),
                )
                _c.commit()
        else:
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
            with db_tx(self.db_path) as conn:
                for r in conn.execute(
                    f"SELECT id FROM {tbl} WHERE {' AND '.join(conds)}", params
                ).fetchall():
                    ids.append(r["id"])
        return ids

    def retrieve(self, query: str, enterprise_id: str, top_k: int = 5,
                 filters: Optional[dict] = None,
                 kind_filter: Optional[List[str]] = None,
                 kind_weight: Optional[dict] = None) -> List[CorpusHit]:
        """混合检索。

        kind_filter: 仅返回 meta.kind ∈ 该列表的命中（按内容类型路由，F3）。
        kind_weight: {kind: 倍率} 对命中分数乘性加权（如育儿问答提高 article 权重），F3。
        二者默认 None → 不过滤/不加权重，完全向后兼容。
        """
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
            with db_tx(self.db_path) as conn:
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
        with db_tx(self.db_path) as conn:
            for cid, score in fused:
                row = conn.execute(
                    "SELECT id, part, enterprise_id, title, content, meta_json, "
                    "product_id, chunk FROM corpus WHERE id=?",
                    (cid,),
                ).fetchone()
                if not row:
                    continue
                # HQ 共享库（enterprise_id == HQ_ENT）对所有企业可见；其余仅本企业可见。
                # 修复 F6：原条件把 HQ 行（"hq"）当成「异企业」直接丢弃，导致总部知识库
                # 跨企业不可读，废掉了「总部资料库一键共享」设计。
                if (row["enterprise_id"] is not None
                        and row["enterprise_id"] != HQ_ENT
                        and row["enterprise_id"] != enterprise_id):
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
            g_kind = g["meta"].get("kind", "")
            # 路由（F3）：限定内容类型则丢弃非匹配 kind
            if kind_filter is not None and g_kind not in kind_filter:
                continue
            # 加权（F3）：按 kind 乘性提升/压低（如育儿问答提高 article 权重）
            w = kind_weight.get(g_kind, 1.0) if kind_weight else 1.0
            final = (g["best_score"]
                     * (PRODUCT_BOOST if g["part"] in ("b_milk", "b_nutrition") else 1.0)
                     * w)
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

