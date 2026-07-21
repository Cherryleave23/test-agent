"""宝宝/客户档案持久化（MOD-baby-profile，P2）。

按 (enterprise_id, employee_id) 隔离（与 SessionStore 一致），跨员工不可见。
客户为 1→N 宝宝的一等实体；所有写入经 WAL 连接，复用 common.db.connect。
"""
from __future__ import annotations

import json
import time
from typing import List, Optional

from common.db import connect
from baby.models import BabyProfile, Customer
from common.crypto import get_vault
import threading
import time


def _norm(s: str) -> str:
    import re
    return re.sub(r"\s+", "", (s or "").strip()).lower()


# ---- P0-3 健康数据加密：落库边界加/解密 ---------------------------------
# 敏感健康字段（baby_age/gender/stage/allergens_json/budget/brand_preference_json/
# category/health_notes/birth_date/gestational_weeks/medical_history_json/
# feeding_history_json）在写入时加密、读取时解密；name/customer_id 等查询/实体
# 链接必需字段保持明文。Vault 缺 key 时开发模式用确定性 dev key（见 common.crypto）。
def _enc(value) -> object:
    """非空值加密为带前缀 token；空值/None 保持原样（NULL/空串）。"""
    if value is None or value == "":
        return value
    return get_vault().encrypt(str(value))


def _dec(raw, cast=str):
    """解密；旧库数字/空值/无前缀明文原样返回。"""
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw  # 旧库数字（budget/gestational_weeks）
    if raw == "":
        return "" if cast is str else None
    return cast(get_vault().decrypt(raw))


class BabyProfileStore:
    # 进程级、按 baby_id 的写锁注册表：串行化同一宝宝的 upsert/merge/delete，
    # 防跨会话（同员工不同 conv）并发写竞态（缺陷 C）。
    _baby_locks: dict = {}
    _lock_guard = threading.Lock()

    @classmethod
    def _lock_for_baby(cls, baby_id: int) -> threading.Lock:
        with cls._lock_guard:
            return cls._baby_locks.setdefault(baby_id, threading.Lock())

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self) -> None:
        with connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS customers (
                    customer_id INTEGER PRIMARY KEY,
                    enterprise_id TEXT NOT NULL,
                    employee_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    phone TEXT,
                    notes TEXT,
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS babies (
                    baby_id INTEGER PRIMARY KEY,
                    enterprise_id TEXT NOT NULL,
                    employee_id TEXT NOT NULL,
                    customer_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    baby_age TEXT,
                    gender TEXT,
                    stage TEXT,
                    allergens_json TEXT,
                    budget REAL,
                    brand_preference_json TEXT,
                    category TEXT,
                    health_notes TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at REAL,
                    updated_at REAL,
                    FOREIGN KEY(customer_id) REFERENCES customers(customer_id)
                );
                CREATE INDEX IF NOT EXISTS idx_babies_emp ON babies(enterprise_id, employee_id);
                CREATE INDEX IF NOT EXISTS idx_customers_emp ON customers(enterprise_id, employee_id);
                """
            )
            # P2-v2 迁移：为旧库自动添加新列（SQLite ALTER TABLE ADD COLUMN）
            cols = {r[1] for r in conn.execute("PRAGMA table_info(babies)").fetchall()}
            if "birth_date" not in cols:
                conn.execute("ALTER TABLE babies ADD COLUMN birth_date TEXT")
            if "gestational_weeks" not in cols:
                conn.execute("ALTER TABLE babies ADD COLUMN gestational_weeks INTEGER")
            if "medical_history_json" not in cols:
                conn.execute("ALTER TABLE babies ADD COLUMN medical_history_json TEXT")
            if "feeding_history_json" not in cols:
                conn.execute("ALTER TABLE babies ADD COLUMN feeding_history_json TEXT")
            conn.commit()

    # ------------------------------------------------------------------
    # customers
    # ------------------------------------------------------------------
    def get_or_create_customer(self, ent: str, emp: str, name: str) -> int:
        """按 (ent, emp, name) 去重取/建客户，返回 customer_id。"""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT customer_id FROM customers "
                "WHERE enterprise_id=? AND employee_id=? AND name=?",
                (ent, emp, name),
            ).fetchone()
            if row:
                return row["customer_id"]
            cur = conn.execute(
                "INSERT INTO customers(enterprise_id, employee_id, name, created_at) "
                "VALUES(?,?,?,?)",
                (ent, emp, name, time.time()),
            )
            conn.commit()
            return cur.lastrowid

    def get_customer(self, customer_id: int) -> Optional[Customer]:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM customers WHERE customer_id=?", (customer_id,)
            ).fetchone()
        if not row:
            return None
        return Customer(
            customer_id=row["customer_id"], enterprise_id=row["enterprise_id"],
            employee_id=row["employee_id"], name=row["name"],
            phone=row["phone"] or "", notes=row["notes"] or "",
        )

    def update_customer_name(self, customer_id: int, name: str) -> None:
        """更新客户名（D2 修复：已建档宝宝客户名后续补充）。

        当自动建档时客户名为「（未命名客户）」，后续消息提供真实客户名时调用。
        """
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE customers SET name=? WHERE customer_id=?",
                (name, customer_id),
            )
            conn.commit()

    def update_baby_customer(self, baby_id: int, customer_id: int) -> None:
        """更新宝宝的客户关联（D2 修复：避免共享 customer 记录导致串档）。

        多个宝宝建档时客户名均为「（未命名客户）」会共享同一 customer 记录。
        直接更新该 customer 记录的 name 会影响所有共享宝宝。
        本方法创建新的 customer 记录并更新 baby 的 customer_id 关联，避免串档。
        """
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE babies SET customer_id=? WHERE baby_id=?",
                (customer_id, baby_id),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # babies
    # ------------------------------------------------------------------
    def create_baby(self, baby: BabyProfile) -> int:
        with connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO babies(enterprise_id, employee_id, customer_id, name,
                   baby_age, gender, stage, allergens_json, budget, brand_preference_json,
                   category, health_notes, status, created_at, updated_at,
                   birth_date, gestational_weeks, medical_history_json, feeding_history_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (baby.enterprise_id, baby.employee_id, baby.customer_id, baby.name,
                 _enc(baby.baby_age), _enc(baby.gender), _enc(baby.stage),
                 _enc(json.dumps(baby.allergens, ensure_ascii=False)),
                 _enc(baby.budget), _enc(json.dumps(baby.brand_preference, ensure_ascii=False)),
                 _enc(baby.category), _enc(baby.health_notes), baby.status, time.time(), time.time(),
                 _enc(baby.birth_date), _enc(baby.gestational_weeks),
                 _enc(json.dumps(baby.medical_history, ensure_ascii=False)),
                 _enc(json.dumps(baby.feeding_history, ensure_ascii=False))),
            )
            conn.commit()
            return cur.lastrowid

    def get_baby(self, baby_id: int) -> Optional[BabyProfile]:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM babies WHERE baby_id=?", (baby_id,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_baby(row)

    @staticmethod
    def _row_to_baby(row) -> BabyProfile:
        return BabyProfile(
            baby_id=row["baby_id"], enterprise_id=row["enterprise_id"],
            employee_id=row["employee_id"], customer_id=row["customer_id"],
            name=row["name"],
            baby_age=_dec(row["baby_age"]),
            gender=_dec(row["gender"]),
            stage=_dec(row["stage"]),
            allergens=json.loads(_dec(row["allergens_json"]) or "[]"),
            budget=_dec(row["budget"], cast=float),
            brand_preference=json.loads(_dec(row["brand_preference_json"]) or "[]"),
            category=_dec(row["category"]),
            health_notes=_dec(row["health_notes"]),
            birth_date=_dec(row["birth_date"]),
            gestational_weeks=_dec(row["gestational_weeks"], cast=int),
            medical_history=json.loads(_dec(row["medical_history_json"]) or "[]"),
            feeding_history=json.loads(_dec(row["feeding_history_json"]) or "[]"),
            status=row["status"] or "pending",
        )

    def upsert_baby_attrs(self, baby_id: int, attrs: BabyProfile) -> BabyProfile:
        """把 attrs 中的非空字段 merge 进 baby_id 档案并返回最新档案（主动归档）。"""
        with self._lock_for_baby(baby_id):
            cur = self.get_baby(baby_id)
            if cur is None:
                raise KeyError(f"baby_id 不存在: {baby_id}")
            merged = cur.merge(attrs)
            with connect(self.db_path) as conn:
                conn.execute(
                    """UPDATE babies SET customer_id=?, name=?, baby_age=?, gender=?, stage=?,
                       allergens_json=?, budget=?, brand_preference_json=?, category=?,
                       health_notes=?, status=?, updated_at=?,
                       birth_date=?, gestational_weeks=?,
                       medical_history_json=?, feeding_history_json=?
                       WHERE baby_id=?""",
                    (merged.customer_id, merged.name, _enc(merged.baby_age), _enc(merged.gender),
                     _enc(merged.stage), _enc(json.dumps(merged.allergens, ensure_ascii=False)),
                     _enc(merged.budget), _enc(json.dumps(merged.brand_preference, ensure_ascii=False)),
                     _enc(merged.category), _enc(merged.health_notes), merged.status, time.time(),
                     _enc(merged.birth_date), _enc(merged.gestational_weeks),
                     _enc(json.dumps(merged.medical_history, ensure_ascii=False)),
                     _enc(json.dumps(merged.feeding_history, ensure_ascii=False)),
                     baby_id),
                )
                conn.commit()
            return merged

    def mark_confirmed(self, baby_id: int) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE babies SET status='confirmed', updated_at=? WHERE baby_id=?",
                (time.time(), baby_id),
            )
            conn.commit()

    def merge_baby(self, target_id: int, source_id: int) -> BabyProfile:
        """把 source 档案合并进 target 并删除 source（自然语言修正安全网）。"""
        # 按 id 升序加锁，避免两个并发 merge 形成死锁
        for bid in sorted({target_id, source_id}):
            self._lock_for_baby(bid).acquire()
        try:
            target = self.get_baby(target_id)
            source = self.get_baby(source_id)
            if target is None or source is None:
                raise KeyError("merge 需两端均存在")
            merged = target.merge(source)
            with connect(self.db_path) as conn:
                conn.execute(
                    """UPDATE babies SET customer_id=?, name=?, baby_age=?, gender=?, stage=?,
                       allergens_json=?, budget=?, brand_preference_json=?, category=?,
                       health_notes=?, status=?, updated_at=?,
                       birth_date=?, gestational_weeks=?,
                       medical_history_json=?, feeding_history_json=?
                       WHERE baby_id=?""",
                    (merged.customer_id, merged.name, _enc(merged.baby_age), _enc(merged.gender),
                     _enc(merged.stage), _enc(json.dumps(merged.allergens, ensure_ascii=False)),
                     _enc(merged.budget), _enc(json.dumps(merged.brand_preference, ensure_ascii=False)),
                     _enc(merged.category), _enc(merged.health_notes), merged.status, time.time(),
                     _enc(merged.birth_date), _enc(merged.gestational_weeks),
                     _enc(json.dumps(merged.medical_history, ensure_ascii=False)),
                     _enc(json.dumps(merged.feeding_history, ensure_ascii=False)),
                     target_id),
                )
                conn.execute("DELETE FROM babies WHERE baby_id=?", (source_id,))
                conn.commit()
            return merged
        finally:
            for bid in sorted({target_id, source_id}):
                self._lock_for_baby(bid).release()

    def delete_baby(self, baby_id: int) -> None:
        with self._lock_for_baby(baby_id):
            with connect(self.db_path) as conn:
                conn.execute("DELETE FROM babies WHERE baby_id=?", (baby_id,))
                conn.commit()

    def find_baby_by_name(self, ent: str, emp: str, name: str) -> Optional[int]:
        """自动建档去重：仅匹配 **已确认(confirmed)** 且**唯一**的同名宝宝。

        防 pending 污染（缺陷 B）：旧 pending 同名档案不参与自动复用——新真实宝宝不会被
        误并入别人的待确认档案；同名多客户（歧义）亦不自动匹配，交回焦点/显式建档。
        """
        nb = _norm(name)
        if not nb:
            return None
        confirmed = [
            it for it in self.list_for_employee(ent, emp)
            if _norm(it.get("baby_name", "")) == nb and it.get("status") == "confirmed"
        ]
        if len(confirmed) == 1:
            return confirmed[0].get("baby_id")
        return None

    def prune_stale_pending(self, days: float = 30) -> int:
        """清理过期待确认(pending)档案，防止无限累积（缺陷 B 的清理机制）。

        仅删 pending 且 created_at 早于 cutoff 的；confirmed 永不自动删。
        返回删除条数。
        """
        cutoff = time.time() - days * 86400.0
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM babies WHERE status='pending' "
                "AND created_at IS NOT NULL AND created_at < ?",
                (cutoff,),
            )
            n = cur.rowcount
            conn.commit()
            return n

    def list_for_employee(self, ent: str, emp: str) -> List[dict]:
        """返回该员工 (客户→宝宝) 清单，供 LLM 消歧上下文（不含无关员工/企业）。"""
        out: List[dict] = []
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT b.baby_id, b.name AS baby_name, b.baby_age, b.stage,
                          b.allergens_json, b.budget, b.category, b.status,
                          c.customer_id, c.name AS customer_name
                   FROM babies b JOIN customers c ON b.customer_id=c.customer_id
                   WHERE b.enterprise_id=? AND b.employee_id=?
                   ORDER BY c.customer_id, b.baby_id""",  # P0：稳定排序，保证 known_json 序列化一致（Prompt Caching 命中前提）
                (ent, emp),
            ).fetchall()
        for r in rows:
            out.append({
                "baby_id": r["baby_id"], "baby_name": r["baby_name"],
                "customer_id": r["customer_id"], "customer_name": r["customer_name"],
                "baby_age": _dec(r["baby_age"]),
                "stage": _dec(r["stage"]),
                "allergens": json.loads(_dec(r["allergens_json"]) or "[]"),
                "budget": _dec(r["budget"], cast=float),
                "category": _dec(r["category"]),
                "status": r["status"] or "pending",
            })
        return out
