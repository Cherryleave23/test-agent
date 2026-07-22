"""处理标记：<repo>/.dataproc/processed.db（SQLite），以 相对路径+内容哈希 为键防重复。"""
import hashlib
import os
import sqlite3

from .repos import get_repo
from .util import now_iso


def _db_path(repo_dir: str) -> str:
    return os.path.join(repo_dir, ".dataproc", "processed.db")


def _conn(repo_dir: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_db_path(repo_dir)), exist_ok=True)
    conn = sqlite3.connect(_db_path(repo_dir))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS processed (
            rel_path TEXT PRIMARY KEY,
            content_hash TEXT,
            status TEXT,
            bundle_ref TEXT,
            updated_at TEXT
        )"""
    )
    return conn


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def is_processed(name: str, rel_path: str, base: str = None) -> bool:
    repo_dir, _meta = get_repo(name, base)
    conn = _conn(repo_dir)
    try:
        row = conn.execute(
            "SELECT content_hash, status FROM processed WHERE rel_path=?", (rel_path,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return False
    cur = file_hash(os.path.join(repo_dir, rel_path))
    return row[0] == cur and row[1] == "processed"


def mark_processed(name: str, rel_path: str, status: str, bundle_ref: str, base: str = None) -> None:
    repo_dir, _meta = get_repo(name, base)
    conn = _conn(repo_dir)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO processed(rel_path, content_hash, status, bundle_ref, updated_at) "
            "VALUES(?,?,?,?,?)",
            (rel_path, file_hash(os.path.join(repo_dir, rel_path)), status, bundle_ref, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def list_markers(name: str, base: str = None) -> list:
    repo_dir, _meta = get_repo(name, base)
    conn = _conn(repo_dir)
    try:
        rows = conn.execute("SELECT rel_path, status, bundle_ref FROM processed").fetchall()
    finally:
        conn.close()
    return [{"path": r[0], "status": r[1], "bundle_ref": r[2]} for r in rows]


def clear_markers(name: str, base: str = None) -> int:
    """清除所有处理标记。返回删除条数。"""
    repo_dir, _meta = get_repo(name, base)
    conn = _conn(repo_dir)
    try:
        cur = conn.execute("DELETE FROM processed")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
