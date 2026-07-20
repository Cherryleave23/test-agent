"""SQLite 连接工厂：提供单实例连接（结构化产品表 / 会话 / FTS5 关键词索引）。

向量检索由 Chroma 承担（O1/D2）；SQLite 仅存结构化数据 + FTS5 关键词索引。
每实例单 SQLite 文件 = 企业隔离（O1/D2）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from contextlib import contextmanager


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开（或创建）一个 SQLite 连接，启用外键、WAL。"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def db_tx(db_path: str | Path):
    """事务上下文：自动提交/回滚。"""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
