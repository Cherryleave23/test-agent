"""Admin 模块数据模型与 DB schema（从 server.py 拆分）。"""
from __future__ import annotations

import time
from pydantic import BaseModel, field_validator, Field

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
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1, le=32768)

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        allowed = {"mock", "ollama", "cloud"}
        if v not in allowed:
            raise ValueError(f"kind 必须是 {allowed} 之一")
        return v


class StoreCreate(BaseModel):
    enterprise_id: str = Field(min_length=1)
    enterprise_name: str = Field(min_length=1)
    db_path: str = "instance.db"


class EmployeeCreate(BaseModel):
    enterprise_id: str = Field(min_length=1)
    employee_id: str = Field(min_length=1)
    employee_name: str = Field(min_length=1)


class GatewayBinding(BaseModel):
    enterprise_id: str = Field(min_length=1)
    employee_id: str = Field(min_length=1)
    wechat_name: str = ""
    bot_token: str = Field(min_length=1)


def validate_table(table: str) -> str:
    """验证表名在白名单中，防注入。"""
    if table not in ALLOWED_TABLES:
        raise ValueError(f"非法表名: {table}，允许: {ALLOWED_TABLES}")
    return table


def mask_token(token: str) -> str:
    """脱敏 bot_token：保留前 4 + 后 4 字符，中间用 * 填充。

    P2-03: 原 mask 仅保留前 8 字符，对短 token 泄露过多。
    """
    if not token:
        return ""
    if len(token) <= 12:
        return token[:2] + "*" * (len(token) - 2)
    return token[:4] + "*" * (len(token) - 8) + token[-4:]
