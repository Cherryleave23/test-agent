"""Admin 模块数据模型与 DB schema（从 server.py 拆分）。"""
from __future__ import annotations

import time
from pydantic import BaseModel

from common.db import connect


ADMIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_stores (
    enterprise_id TEXT PRIMARY KEY,
    enterprise_name TEXT NOT NULL,
    db_path TEXT NOT NULL,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS admin_employees (
    id INTEGER PRIMARY KEY,
    enterprise_id TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    employee_name TEXT NOT NULL,
    wechat_name TEXT,
    bot_token TEXT,
    bound_at REAL,
    UNIQUE(enterprise_id, employee_id)
);
"""

# 允许 confirm/delete 的表白名单（防 SQL 注入/越权操作）
ALLOWED_TABLES = frozenset({"products_milk", "products_nutrition"})


def init_admin_db(db_path: str):
    """初始化 admin 表（幂等）。"""
    with connect(db_path) as conn:
        conn.executescript(ADMIN_SCHEMA)
        conn.commit()


class LLMConfigUpdate(BaseModel):
    kind: str = "mock"
    base_url: str = ""
    model: str = "default"
    api_key: str = ""
    temperature: float = 0.2
    max_tokens: int = 1024


class StoreCreate(BaseModel):
    enterprise_id: str
    enterprise_name: str
    db_path: str = "instance.db"


class EmployeeCreate(BaseModel):
    enterprise_id: str
    employee_id: str
    employee_name: str


class GatewayBinding(BaseModel):
    enterprise_id: str
    employee_id: str
    wechat_name: str = ""
    bot_token: str


def validate_table(table: str) -> str:
    """验证表名在白名单中，防注入。"""
    if table not in ALLOWED_TABLES:
        raise ValueError(f"非法表名: {table}，允许: {ALLOWED_TABLES}")
    return table


def mask_token(token: str) -> str:
    """脱敏 bot_token：保留前 8 字符 + 省略号。"""
    if not token:
        return ""
    return token[:8] + "…"
