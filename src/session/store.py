"""会话隔离存储（MOD-session，G4/D4）。

会话主键 = (enterprise_id, employee_id, conversation_id)，employee_id = iLink from_user_id。
保证多员工互不串扰：取历史/落盘均按主键隔离。
每会话一把 asyncio.Lock 串行化并发轮次；message_id 去重防重复处理（flight dedup）。
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from typing import List, Tuple, Optional

from common.db import connect
from session.constraints import UserConstraints


@dataclass
class Turn:
    role: str
    content: str
    ts: float


class SessionStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_guard = asyncio.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id INTEGER PRIMARY KEY,
                    enterprise_id TEXT NOT NULL,
                    employee_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    created_at REAL,
                    UNIQUE(enterprise_id, employee_id, conversation_id)
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY,
                    session_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    ts REAL,
                    message_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
                CREATE TABLE IF NOT EXISTS constraints (
                    session_id INTEGER PRIMARY KEY,
                    json TEXT
                );
                CREATE TABLE IF NOT EXISTS session_baby_focus (
                    session_id INTEGER PRIMARY KEY,
                    baby_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS session_resolution_fails (
                    session_id INTEGER PRIMARY KEY,
                    fails INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            conn.commit()

    def session_key(self, enterprise_id: str, employee_id: str, conversation_id: str) -> str:
        return f"{enterprise_id}:{employee_id}:{conversation_id}"

    async def lock_for(self, key: str) -> asyncio.Lock:
        async with self._lock_guard:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    def get_or_create(self, enterprise_id: str, employee_id: str, conversation_id: str) -> int:
        with connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT session_id FROM sessions "
                "WHERE enterprise_id=? AND employee_id=? AND conversation_id=?",
                (enterprise_id, employee_id, conversation_id),
            )
            row = cur.fetchone()
            if row:
                return row["session_id"]
            cur.execute(
                "INSERT INTO sessions(enterprise_id, employee_id, conversation_id, created_at) "
                "VALUES(?,?,?,?)",
                (enterprise_id, employee_id, conversation_id, time.time()),
            )
            conn.commit()
            return cur.lastrowid

    def seen_message(self, session_id: int, message_id: str) -> bool:
        """去重：该 message_id 是否已处理（防止重复轮次 / 重试重放）。"""
        if not message_id:
            return False
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM turns WHERE session_id=? AND message_id=? LIMIT 1",
                (session_id, message_id),
            ).fetchone()
            return row is not None

    def append_turn(self, session_id: int, role: str, content: str,
                    message_id: Optional[str] = None) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO turns(session_id, role, content, ts, message_id) "
                "VALUES(?,?,?,?,?)",
                (session_id, role, content, time.time(), message_id),
            )
            conn.commit()

    def history(self, session_id: int, limit: int = 20) -> List[Turn]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT role, content, ts FROM turns WHERE session_id=? "
                "ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [Turn(r["role"], r["content"], r["ts"]) for r in reversed(rows)]

    def reset(self, session_id: int) -> None:
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM turns WHERE session_id=?", (session_id,))
            conn.commit()

    # ---------- 用户约束持久化（P1：方向 B 累积 + 方向 A 压缩，跨轮可续）----------
    def get_constraints(self, session_id: int) -> Optional[UserConstraints]:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT json FROM constraints WHERE session_id=?", (session_id,)
            ).fetchone()
        if row is None or not row["json"]:
            return None
        return UserConstraints.from_json(row["json"])

    def save_constraints(self, session_id: int, constraints: UserConstraints) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO constraints(session_id, json) VALUES(?, ?)",
                (session_id, constraints.to_json()),
            )
            conn.commit()

    # ---------- 本会话焦点宝宝（MOD-baby-profile：代词/快速切换消歧）----------
    def get_focus_baby(self, session_id: int) -> Optional[int]:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT baby_id FROM session_baby_focus WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return row["baby_id"] if row else None

    def set_focus_baby(self, session_id: int, baby_id: int) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO session_baby_focus(session_id, baby_id) "
                "VALUES(?, ?)",
                (session_id, baby_id),
            )
            conn.commit()

    # ---------- 宝宝消歧连续失败计数（熔断统计，缺陷 A）----------
    def get_resolution_fails(self, session_id: int) -> int:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT fails FROM session_resolution_fails WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return row["fails"] if row else 0

    def inc_resolution_fails(self, session_id: int) -> int:
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO session_resolution_fails(session_id, fails) VALUES(?, 1) "
                "ON CONFLICT(session_id) DO UPDATE SET fails = fails + 1",
                (session_id,),
            )
            conn.commit()
            return cur.rowcount and self.get_resolution_fails(session_id)

    def reset_resolution_fails(self, session_id: int) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE session_resolution_fails SET fails=0 WHERE session_id=?",
                (session_id,),
            )
            conn.commit()
