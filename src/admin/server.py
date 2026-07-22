"""WebUI 管理后台（MOD-admin）：FastAPI 服务，5 大管理板块。

板块：
  1. LLM 选择 — 查看/切换当前实例的 LLM provider 配置
  2. 数据库加载 — 触发 scan_and_load 扫收件箱加载 bundle，查看已加载状态
  3. 门店创立与员工管理 — 企业/员工 CRUD（SQLite）
  4. 员工的微信网关绑定 — iLink Bot Token 绑定与查看
  5. 宝宝档案查看 — 列出/查看宝宝档案（只读）

启动方式：
  python -m admin.server --config deploy/enterprise.yaml --port 8090

安全设计：
  - Bearer Token 认证：AGENT_ADMIN_TOKEN 环境变量设置，所有 API 端点需携带
  - 本地绑定：默认 127.0.0.1，不暴露外网
  - HTML 转义：所有动态值经 html.escape() 防 XSS
  - YAML 路径验证：仅允许 .yaml/.yml 后缀，拒绝路径遍历（..）
  - 表名白名单：confirm/delete 仅允许 products_milk / products_nutrition
  - 租户隔离：所有查询强制使用 cfg.enterprise_id，不接受外部传入
"""
from __future__ import annotations

import os
import sys
import time
import argparse
import secrets
import logging
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# 确保 src 在 path
_here = os.path.dirname(os.path.abspath(__file__))
_src = os.path.dirname(_here)
if _src not in sys.path:
    sys.path.insert(0, _src)

from common.config import EnterpriseConfig
from common.db import connect, db_tx
from kb.store import KnowledgeStore
from baby.store import BabyProfileStore
from ingest.importer import scan_and_load
from admin.models import (
    init_admin_db, LLMConfigUpdate, StoreCreate, EmployeeCreate, GatewayBinding,
    validate_table, mask_token, ALLOWED_TABLES,
)
from admin.pages import (
    render_dashboard, render_llm_page, render_database_page,
    render_stores_page, render_gateway_page, render_babies_page,
)

logger = logging.getLogger(__name__)

# ---------- 安全：Bearer Token 认证 ----------
_security = HTTPBearer(auto_error=False)


def _verify_token(credentials: HTTPAuthorizationCredentials = Security(_security)):
    """验证 Bearer Token。无 token 配置时放行（开发模式）。"""
    expected = os.environ.get("AGENT_ADMIN_TOKEN", "").strip()
    if not expected:
        return  # 开发模式无 token，放行
    if credentials is None or not secrets.compare_digest(credentials.credentials, expected):
        logger.warning("认证失败: token 不匹配")
        raise HTTPException(401, "未授权：请提供有效的 Bearer Token")


# ---------- YAML 路径验证 ----------
def _validate_yaml_path(yaml_path: str) -> str:
    """验证 yaml 路径合法（防路径遍历，允许 .yaml/.yml 后缀）。

    路径来源为 AGENT_CONFIG_PATH 环境变量（启动时设定，非 API 用户输入）。
    """
    if not yaml_path:
        raise HTTPException(400, "配置文件路径为空")
    abs_path = os.path.abspath(yaml_path)
    # 拒绝路径遍历
    if ".." in yaml_path:
        raise HTTPException(403, f"配置文件路径不允许包含 ..: {abs_path}")
    # 仅允许 yaml/yml 后缀
    if not abs_path.endswith((".yaml", ".yml")):
        raise HTTPException(403, f"配置文件必须是 .yaml 或 .yml 后缀: {abs_path}")
    return abs_path


# ---------- FastAPI App ----------
def create_app(cfg: EnterpriseConfig) -> FastAPI:
    app = FastAPI(title="母婴 Agent 管理后台", version="0.3.0")
    admin_db = cfg.db_path
    init_admin_db(admin_db)

    _store_holder: dict = {"store": None}
    _store_lock = threading.Lock()

    def _get_store() -> KnowledgeStore:
        if _store_holder["store"] is None:
            with _store_lock:
                if _store_holder["store"] is None:  # double-check
                    rerank_kind = cfg.rerank.kind if cfg.rerank.kind != "mock" else "none"
                    _store_holder["store"] = KnowledgeStore(
                        cfg.db_path, embedding_kind=cfg.embedding.kind,
                        rerank_kind=rerank_kind,
                    )
        return _store_holder["store"]

    def _get_baby_store() -> BabyProfileStore:
        return BabyProfileStore(cfg.baby_db_path or cfg.db_path)

    # ===== 页面（无需认证，仅 HTML 界面）=====
    @app.get("/", response_class=HTMLResponse)
    def index():
        return render_dashboard(cfg)

    @app.get("/admin/llm", response_class=HTMLResponse)
    def page_llm():
        return render_llm_page(cfg)

    @app.get("/admin/database", response_class=HTMLResponse)
    def page_database():
        return render_database_page(cfg)

    @app.get("/admin/stores", response_class=HTMLResponse)
    def page_stores():
        return render_stores_page(cfg)

    @app.get("/admin/gateway", response_class=HTMLResponse)
    def page_gateway():
        return render_gateway_page(cfg)

    @app.get("/admin/babies", response_class=HTMLResponse)
    def page_babies():
        return render_babies_page(cfg)

    # ===== API: LLM 选择 =====
    @app.get("/api/llm", dependencies=[Depends(_verify_token)])
    def get_llm_config():
        return {
            "kind": cfg.llm.kind,
            "base_url": cfg.llm.base_url or "",
            "model": cfg.llm.model,
            "api_key": ("<set>" if cfg.llm.api_key else ""),
            "temperature": cfg.llm.temperature,
            "max_tokens": cfg.llm.max_tokens,
        }

    @app.post("/api/llm", dependencies=[Depends(_verify_token)])
    def update_llm_config(update: LLMConfigUpdate):
        """更新 LLM 配置（写入 yaml 文件，需重启 agent 生效）。"""
        yaml_path = os.environ.get("AGENT_CONFIG_PATH", "deploy/enterprise.yaml")
        abs_path = _validate_yaml_path(yaml_path)
        import yaml as _yaml
        data: dict = {}
        if os.path.isfile(abs_path):
            with open(abs_path, "r", encoding="utf-8") as f:
                data = _yaml.safe_load(f) or {}
        data.setdefault("llm", {})
        data["llm"]["kind"] = update.kind
        data["llm"]["base_url"] = update.base_url or None
        data["llm"]["model"] = update.model
        # 空 api_key 不覆盖已有 key（避免误清空）
        if update.api_key:
            data["llm"]["api_key"] = update.api_key
            # P2-N4: api_key 明文写入 YAML，建议通过 AGENT_LLM_API_KEY 环境变量覆盖
            logger.warning("LLM api_key 已明文写入 %s，建议设置 AGENT_LLM_API_KEY 环境变量替代", abs_path)
        data["llm"]["temperature"] = update.temperature
        data["llm"]["max_tokens"] = update.max_tokens
        with open(abs_path, "w", encoding="utf-8") as f:
            _yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        logger.info("LLM 配置已更新: %s", abs_path)
        return {"status": "ok", "message": "LLM 配置已写入，需重启 agent 生效", "yaml_path": abs_path}

    # ===== API: 数据库加载 =====
    @app.get("/api/database/status", dependencies=[Depends(_verify_token)])
    def db_status():
        _get_store()  # 确保 schema 已初始化
        with db_tx(cfg.db_path) as conn:
            corpus_count = conn.execute("SELECT COUNT(*) FROM corpus").fetchone()[0]
            products_count = conn.execute("SELECT COUNT(*) FROM products_milk").fetchone()[0]
            nutrition_count = conn.execute("SELECT COUNT(*) FROM products_nutrition").fetchone()[0]
        inbox = os.environ.get("BUNDLE_INBOX_DIR", "")
        return {
            "db_path": cfg.db_path,
            "corpus_count": corpus_count,
            "products_milk": products_count,
            "products_nutrition": nutrition_count,
            "bundle_inbox_dir": inbox,
            "inbox_exists": os.path.isdir(inbox) if inbox else False,
        }

    @app.post("/api/database/scan", dependencies=[Depends(_verify_token)])
    def db_scan():
        inbox = os.environ.get("BUNDLE_INBOX_DIR", "")
        if not inbox or not os.path.isdir(inbox):
            raise HTTPException(400, f"收件箱目录未配置或不存在: {inbox}")
        store = _get_store()
        result = scan_and_load(inbox, store, cfg.enterprise_id)
        logger.info("bundle 扫描完成: enterprise_id=%s", cfg.enterprise_id)
        return result

    @app.get("/api/database/pending", dependencies=[Depends(_verify_token)])
    def db_pending():
        store = _get_store()
        return store.list_pending_products(cfg.enterprise_id)

    @app.post("/api/database/confirm", dependencies=[Depends(_verify_token)])
    def db_confirm(product_id: int, value: str, table: str = "products_milk"):
        try:
            validate_table(table)
        except ValueError as e:
            raise HTTPException(400, str(e))
        store = _get_store()
        try:
            store.confirm_product(product_id, value, table, cfg.enterprise_id)
        except PermissionError as e:
            logger.warning("跨租户操作被拒绝: %s", e)
            raise HTTPException(403, "无权操作该商品")
        except ValueError as e:
            raise HTTPException(404, str(e))
        logger.info("商品确认: id=%s table=%s ent=%s", product_id, table, cfg.enterprise_id)
        return {"status": "ok"}

    @app.delete("/api/database/product", dependencies=[Depends(_verify_token)])
    def db_delete(product_id: int, table: str = "products_milk"):
        try:
            validate_table(table)
        except ValueError as e:
            raise HTTPException(400, str(e))
        store = _get_store()
        try:
            store.delete_product(product_id, table, cfg.enterprise_id)
        except PermissionError as e:
            logger.warning("跨租户操作被拒绝: %s", e)
            raise HTTPException(403, "无权操作该商品")
        except ValueError as e:
            raise HTTPException(404, str(e))
        logger.info("商品删除: id=%s table=%s ent=%s", product_id, table, cfg.enterprise_id)
        return {"status": "ok"}

    # ===== API: 门店创立与员工管理 =====
    @app.get("/api/stores", dependencies=[Depends(_verify_token)])
    def list_stores():
        # P2-R4-3: 过滤当前企业，不暴露其他门店
        with db_tx(admin_db) as conn:
            rows = conn.execute(
                "SELECT enterprise_id, enterprise_name, db_path, created_at "
                "FROM admin_stores WHERE enterprise_id=?",
                (cfg.enterprise_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/stores", dependencies=[Depends(_verify_token)])
    def create_store(store: StoreCreate):
        # P2-R4-4: 强制使用当前实例的企业 ID
        with db_tx(admin_db) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO admin_stores(enterprise_id, enterprise_name, db_path, created_at) "
                "VALUES(?,?,?,?)",
                (cfg.enterprise_id, store.enterprise_name, store.db_path, time.time()),
            )
        return {"status": "ok"}

    @app.get("/api/employees", dependencies=[Depends(_verify_token)])
    def list_employees():
        # P0-01: 强制使用当前实例的企业 ID，不接受外部传入
        ent = cfg.enterprise_id
        with db_tx(admin_db) as conn:
            rows = conn.execute(
                "SELECT id, enterprise_id, employee_id, employee_name, wechat_name, bot_token, bound_at "
                "FROM admin_employees WHERE enterprise_id=?",
                (ent,),
            ).fetchall()
        return [{
            "id": r["id"], "enterprise_id": r["enterprise_id"],
            "employee_id": r["employee_id"], "employee_name": r["employee_name"],
            "wechat_name": r["wechat_name"], "bot_token": mask_token(r["bot_token"] or ""),
            "bound_at": r["bound_at"],
        } for r in rows]

    @app.post("/api/employees", dependencies=[Depends(_verify_token)])
    def create_employee(emp: EmployeeCreate):
        # P2-R4-4: 强制使用当前实例的企业 ID，忽略请求体中的 enterprise_id
        with db_tx(admin_db) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO admin_employees(enterprise_id, employee_id, employee_name) "
                "VALUES(?,?,?)",
                (cfg.enterprise_id, emp.employee_id, emp.employee_name),
            )
        return {"status": "ok"}

    @app.delete("/api/employees/{emp_id}", dependencies=[Depends(_verify_token)])
    def delete_employee(emp_id: int):
        # P1-N1: 校验 enterprise_id 防跨租户删除
        with db_tx(admin_db) as conn:
            cur = conn.execute(
                "DELETE FROM admin_employees WHERE id=? AND enterprise_id=?",
                (emp_id, cfg.enterprise_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "员工不存在或无权操作")
        return {"status": "ok"}

    # ===== API: 微信网关绑定 =====
    @app.get("/api/gateway", dependencies=[Depends(_verify_token)])
    def list_gateway_bindings():
        # P0-01: 强制使用当前实例的企业 ID
        ent = cfg.enterprise_id
        with db_tx(admin_db) as conn:
            rows = conn.execute(
                "SELECT id, enterprise_id, employee_id, wechat_name, bot_token, bound_at "
                "FROM admin_employees WHERE enterprise_id=? AND bot_token IS NOT NULL",
                (ent,),
            ).fetchall()
        return [{
            "id": r["id"], "enterprise_id": r["enterprise_id"],
            "employee_id": r["employee_id"], "wechat_name": r["wechat_name"],
            "bot_token": mask_token(r["bot_token"] or ""),
            "bound_at": r["bound_at"],
        } for r in rows]

    @app.post("/api/gateway", dependencies=[Depends(_verify_token)])
    def bind_gateway(binding: GatewayBinding):
        # P2-R4-4: 强制使用当前实例的企业 ID
        ent = cfg.enterprise_id
        with db_tx(admin_db) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO admin_employees(enterprise_id, employee_id, employee_name) "
                "VALUES(?,?,?)",
                (ent, binding.employee_id, binding.wechat_name or ""),
            )
            conn.execute(
                "UPDATE admin_employees SET wechat_name=?, bot_token=?, bound_at=? "
                "WHERE enterprise_id=? AND employee_id=?",
                (binding.wechat_name, binding.bot_token, time.time(),
                 ent, binding.employee_id),
            )
        logger.info("网关绑定: employee_id=%s", binding.employee_id)
        return {"status": "ok", "message": f"员工 {binding.employee_id} 的微信网关已绑定"}

    @app.delete("/api/gateway/{emp_id}", dependencies=[Depends(_verify_token)])
    def unbind_gateway(emp_id: int):
        # P1-N1: 校验 enterprise_id 防跨租户解绑
        with db_tx(admin_db) as conn:
            cur = conn.execute(
                "UPDATE admin_employees SET bot_token=NULL, bound_at=NULL "
                "WHERE id=? AND enterprise_id=?",
                (emp_id, cfg.enterprise_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "网关绑定不存在或无权操作")
        logger.info("网关解绑: emp_id=%s", emp_id)
        return {"status": "ok"}

    # ===== API: 宝宝档案查看（受限字段）=====
    @app.get("/api/babies", dependencies=[Depends(_verify_token)])
    def list_babies(employee_id: Optional[str] = None):
        # P0-01: 强制使用当前实例的企业 ID
        baby_store = _get_baby_store()
        ent = cfg.enterprise_id
        if employee_id:
            items = baby_store.list_for_employee(ent, employee_id)
        else:
            items = baby_store.list_all_for_enterprise(ent)
        # 只展示概览，不含敏感健康详情
        out = []
        for it in items:
            out.append({
                "baby_id": it.get("baby_id"),
                "name": it.get("baby_name", ""),
                "baby_age": it.get("baby_age", ""),
                "gender": it.get("gender", ""),
                "stage": it.get("stage", ""),
                "status": it.get("status", ""),
                "enterprise_id": it.get("enterprise_id", ent),
                "employee_id": it.get("employee_id", ""),
            })
        return out

    @app.get("/api/babies/{baby_id}", dependencies=[Depends(_verify_token)])
    def get_baby_detail(baby_id: int):
        baby_store = _get_baby_store()
        b = baby_store.get_baby(baby_id)
        if b is None:
            raise HTTPException(404, f"宝宝档案不存在: {baby_id}")
        # P1-N2: 校验 enterprise_id 防跨租户访问
        if b.enterprise_id != cfg.enterprise_id:
            logger.warning("跨租户宝宝档案访问被拒绝: baby_id=%s ent=%s", baby_id, cfg.enterprise_id)
            raise HTTPException(403, "无权访问该宝宝档案")
        # 受限字段：不返回 allergens / medical_history / feeding_history 等敏感详情
        return {
            "baby_id": b.baby_id,
            "name": b.name,
            "baby_age": b.baby_age,
            "gender": b.gender,
            "stage": b.stage,
            "budget": b.budget,
            "brand_preference": b.brand_preference,
            "category": b.category,
            "birth_date": b.birth_date,
            "gestational_weeks": b.gestational_weeks,
            "status": b.status,
            "enterprise_id": b.enterprise_id,
            "employee_id": b.employee_id,
        }

    return app


# ---------- CLI 入口 ----------
def main():
    ap = argparse.ArgumentParser(description="WebUI 管理后台")
    ap.add_argument("--config", default="deploy/enterprise.yaml", help="企业配置 yaml 路径")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址")
    ap.add_argument("--port", type=int, default=8090, help="监听端口")
    args = ap.parse_args()

    if os.path.isfile(args.config):
        cfg = EnterpriseConfig.from_yaml_with_env(args.config)
    else:
        cfg = EnterpriseConfig(enterprise_id="ent_demo", enterprise_name="演示企业")

    os.environ["AGENT_CONFIG_PATH"] = args.config

    app = create_app(cfg)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
