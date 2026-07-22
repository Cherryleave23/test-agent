"""WebUI 管理后台（MOD-admin）：FastAPI 服务，5 大管理板块。

板块：
  1. LLM 选择 — 查看/切换当前实例的 LLM provider 配置
  2. 数据库加载 — 触发 scan_and_load 扫收件箱加载 bundle，查看已加载状态
  3. 门店创立与员工管理 — 企业/员工 CRUD（SQLite）
  4. 员工的微信网关绑定 — iLink Bot Token 绑定与查看
  5. 宝宝档案查看 — 列出/查看宝宝档案（只读）

启动方式：
  python -m admin.server --config deploy/enterprise.yaml --port 8090

设计要点：
  - 与 agent 运行时共享同一 EnterpriseConfig + db_path（只读为主，写操作需确认）
  - LLM 配置变更需重启 agent 进程才生效（写 yaml + 提示）
  - 数据库加载调用 importer.scan_and_load，复用 agent 的 store 实例
  - 门店/员工/绑定数据存在 SQLite admin 表中（与 agent 库分离）
"""
from __future__ import annotations

import os
import sys
import json
import time
import argparse
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

# 确保 src 在 path
_here = os.path.dirname(os.path.abspath(__file__))
_src = os.path.dirname(_here)
if _src not in sys.path:
    sys.path.insert(0, _src)

from common.config import EnterpriseConfig
from common.db import connect
from kb.store import KnowledgeStore
from baby.store import BabyProfileStore
from ingest.importer import scan_and_load


# ---------- admin 自管 schema ----------
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


def _init_admin_db(db_path: str):
    with connect(db_path) as conn:
        conn.executescript(ADMIN_SCHEMA)
        conn.commit()


# ---------- Pydantic 模型 ----------
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


# ---------- FastAPI App ----------
def create_app(cfg: EnterpriseConfig) -> FastAPI:
    app = FastAPI(title="母婴 Agent 管理后台", version="0.1.0")
    admin_db = cfg.db_path  # admin 表与 agent 共库，隔离表名
    _init_admin_db(admin_db)

    # 共享 agent 的 store 实例（数据库加载用）
    _store_holder: dict = {"store": None}

    def _get_store() -> KnowledgeStore:
        if _store_holder["store"] is None:
            _store_holder["store"] = KnowledgeStore(
                cfg.db_path, embedding_kind=cfg.embedding.kind,
                rerank_kind=cfg.rerank.kind,
            )
        return _store_holder["store"]

    def _get_baby_store() -> BabyProfileStore:
        return BabyProfileStore(cfg.baby_db_path or cfg.db_path)

    # ===== 页面 =====
    @app.get("/", response_class=HTMLResponse)
    def index():
        return _render_dashboard(cfg)

    @app.get("/admin/llm", response_class=HTMLResponse)
    def page_llm():
        return _render_llm_page(cfg)

    @app.get("/admin/database", response_class=HTMLResponse)
    def page_database():
        return _render_database_page(cfg)

    @app.get("/admin/stores", response_class=HTMLResponse)
    def page_stores():
        return _render_stores_page(cfg)

    @app.get("/admin/gateway", response_class=HTMLResponse)
    def page_gateway():
        return _render_gateway_page(cfg)

    @app.get("/admin/babies", response_class=HTMLResponse)
    def page_babies():
        return _render_babies_page(cfg)

    # ===== API: LLM 选择 =====
    @app.get("/api/llm")
    def get_llm_config():
        return {
            "kind": cfg.llm.kind,
            "base_url": cfg.llm.base_url or "",
            "model": cfg.llm.model,
            "api_key": ("<set>" if cfg.llm.api_key else ""),
            "temperature": cfg.llm.temperature,
            "max_tokens": cfg.llm.max_tokens,
        }

    @app.post("/api/llm")
    def update_llm_config(update: LLMConfigUpdate):
        """更新 LLM 配置（写入 yaml 文件，需重启 agent 生效）。"""
        yaml_path = os.environ.get("AGENT_CONFIG_PATH", "deploy/enterprise.yaml")
        # 读现有 yaml，更新 llm 段
        import yaml as _yaml
        data: dict = {}
        if os.path.isfile(yaml_path):
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = _yaml.safe_load(f) or {}
        data.setdefault("llm", {})
        data["llm"]["kind"] = update.kind
        data["llm"]["base_url"] = update.base_url or None
        data["llm"]["model"] = update.model
        data["llm"]["api_key"] = update.api_key or None
        data["llm"]["temperature"] = update.temperature
        data["llm"]["max_tokens"] = update.max_tokens
        with open(yaml_path, "w", encoding="utf-8") as f:
            _yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        return {"status": "ok", "message": "LLM 配置已写入，需重启 agent 生效", "yaml_path": yaml_path}

    # ===== API: 数据库加载 =====
    @app.get("/api/database/status")
    def db_status():
        store = _get_store()
        with connect(cfg.db_path) as conn:
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

    @app.post("/api/database/scan")
    def db_scan():
        """触发 scan_and_load 扫收件箱加载 bundle。"""
        inbox = os.environ.get("BUNDLE_INBOX_DIR", "")
        if not inbox or not os.path.isdir(inbox):
            raise HTTPException(400, f"收件箱目录未配置或不存在: {inbox}")
        store = _get_store()
        result = scan_and_load(inbox, store, cfg.enterprise_id)
        return result

    @app.get("/api/database/pending")
    def db_pending():
        """列出待确认商品（F5 数据侧）。"""
        store = _get_store()
        return store.list_pending_products(cfg.enterprise_id)

    @app.post("/api/database/confirm")
    def db_confirm(product_id: int, value: str, table: str = "products_milk"):
        """确认 pending 商品。"""
        store = _get_store()
        store.confirm_product(product_id, value, table)
        return {"status": "ok"}

    @app.delete("/api/database/product")
    def db_delete(product_id: int, table: str = "products_milk"):
        """删除商品。"""
        store = _get_store()
        store.delete_product(product_id, table)
        return {"status": "ok"}

    # ===== API: 门店创立与员工管理 =====
    @app.get("/api/stores")
    def list_stores():
        with connect(admin_db) as conn:
            rows = conn.execute(
                "SELECT enterprise_id, enterprise_name, db_path, created_at FROM admin_stores"
            ).fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/stores")
    def create_store(store: StoreCreate):
        with connect(admin_db) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO admin_stores(enterprise_id, enterprise_name, db_path, created_at) "
                "VALUES(?,?,?,?)",
                (store.enterprise_id, store.enterprise_name, store.db_path, time.time()),
            )
            conn.commit()
        return {"status": "ok"}

    @app.get("/api/employees")
    def list_employees(enterprise_id: Optional[str] = None):
        with connect(admin_db) as conn:
            if enterprise_id:
                rows = conn.execute(
                    "SELECT id, enterprise_id, employee_id, employee_name, wechat_name, bot_token, bound_at "
                    "FROM admin_employees WHERE enterprise_id=?",
                    (enterprise_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, enterprise_id, employee_id, employee_name, wechat_name, bot_token, bound_at "
                    "FROM admin_employees"
                ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("bot_token"):
                d["bot_token"] = d["bot_token"][:8] + "…"  # 脱敏
            out.append(d)
        return out

    @app.post("/api/employees")
    def create_employee(emp: EmployeeCreate):
        with connect(admin_db) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO admin_employees(enterprise_id, employee_id, employee_name) "
                "VALUES(?,?,?)",
                (emp.enterprise_id, emp.employee_id, emp.employee_name),
            )
            conn.commit()
        return {"status": "ok"}

    @app.delete("/api/employees/{emp_id}")
    def delete_employee(emp_id: int):
        with connect(admin_db) as conn:
            conn.execute("DELETE FROM admin_employees WHERE id=?", (emp_id,))
            conn.commit()
        return {"status": "ok"}

    # ===== API: 微信网关绑定 =====
    @app.get("/api/gateway")
    def list_gateway_bindings(enterprise_id: Optional[str] = None):
        with connect(admin_db) as conn:
            if enterprise_id:
                rows = conn.execute(
                    "SELECT id, enterprise_id, employee_id, wechat_name, bot_token, bound_at "
                    "FROM admin_employees WHERE enterprise_id=? AND bot_token IS NOT NULL",
                    (enterprise_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, enterprise_id, employee_id, wechat_name, bot_token, bound_at "
                    "FROM admin_employees WHERE bot_token IS NOT NULL"
                ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("bot_token"):
                d["bot_token"] = d["bot_token"][:8] + "…"
            out.append(d)
        return out

    @app.post("/api/gateway")
    def bind_gateway(binding: GatewayBinding):
        with connect(admin_db) as conn:
            # 确保 employee 存在，然后绑定 token
            conn.execute(
                "INSERT OR IGNORE INTO admin_employees(enterprise_id, employee_id, employee_name) "
                "VALUES(?,?,?)",
                (binding.enterprise_id, binding.employee_id, binding.wechat_name or ""),
            )
            conn.execute(
                "UPDATE admin_employees SET wechat_name=?, bot_token=?, bound_at=? "
                "WHERE enterprise_id=? AND employee_id=?",
                (binding.wechat_name, binding.bot_token, time.time(),
                 binding.enterprise_id, binding.employee_id),
            )
            conn.commit()
        return {"status": "ok", "message": f"员工 {binding.employee_id} 的微信网关已绑定"}

    @app.delete("/api/gateway/{emp_id}")
    def unbind_gateway(emp_id: int):
        with connect(admin_db) as conn:
            conn.execute(
                "UPDATE admin_employees SET bot_token=NULL, bound_at=NULL WHERE id=?",
                (emp_id,),
            )
            conn.commit()
        return {"status": "ok"}

    # ===== API: 宝宝档案查看 =====
    @app.get("/api/babies")
    def list_babies(enterprise_id: Optional[str] = None, employee_id: Optional[str] = None):
        baby_store = _get_baby_store()
        ent = enterprise_id or cfg.enterprise_id
        if employee_id:
            items = baby_store.list_for_employee(ent, employee_id)
        else:
            # 管理后台：无 employee_id 时列出该企业全部宝宝
            items = baby_store.list_all_for_enterprise(ent)
        # 脱敏：隐藏详细健康信息，只展示概览
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

    @app.get("/api/babies/{baby_id}")
    def get_baby_detail(baby_id: int):
        baby_store = _get_baby_store()
        b = baby_store.get_baby(baby_id)
        if b is None:
            raise HTTPException(404, f"宝宝档案不存在: {baby_id}")
        return {
            "baby_id": b.baby_id,
            "name": b.name,
            "baby_age": b.baby_age,
            "gender": b.gender,
            "stage": b.stage,
            "allergens": b.allergens,
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


# ---------- HTML 页面渲染 ----------
_BASE_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif;
       background: #f5f7fa; color: #333; }
.navbar { background: #2563eb; color: #fff; padding: 12px 24px; display: flex; gap: 24px; }
.navbar a { color: #fff; text-decoration: none; padding: 6px 12px; border-radius: 4px; }
.navbar a:hover, .navbar a.active { background: rgba(255,255,255,0.2); }
.container { max-width: 960px; margin: 24px auto; padding: 0 16px; }
.card { background: #fff; border-radius: 8px; padding: 20px; margin-bottom: 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
h1 { font-size: 20px; margin-bottom: 16px; }
h2 { font-size: 16px; margin-bottom: 12px; color: #555; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #e5e7eb; font-size: 14px; }
th { background: #f9fafb; font-weight: 600; }
.btn { display: inline-block; padding: 6px 16px; background: #2563eb; color: #fff;
       border: none; border-radius: 4px; cursor: pointer; font-size: 14px; }
.btn:hover { background: #1d4ed8; }
.btn-danger { background: #dc2626; }
.btn-danger:hover { background: #b91c1c; }
.form-group { margin-bottom: 12px; }
.form-group label { display: block; margin-bottom: 4px; font-size: 14px; font-weight: 500; }
.form-group input, .form-group select { width: 100%; padding: 8px; border: 1px solid #d1d5db;
       border-radius: 4px; font-size: 14px; }
.stat { display: inline-block; padding: 16px 24px; background: #eff6ff; border-radius: 8px;
        margin-right: 16px; }
.stat-num { font-size: 28px; font-weight: 700; color: #2563eb; }
.stat-label { font-size: 12px; color: #6b7280; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; }
.badge-confirmed { background: #d1fae5; color: #065f46; }
.badge-pending { background: #fef3c7; color: #92400e; }
"""


def _render_nav(active: str = "") -> str:
    items = [
        ("", "仪表盘"), ("llm", "LLM 选择"), ("database", "数据库加载"),
        ("stores", "门店与员工"), ("gateway", "微信网关绑定"), ("babies", "宝宝档案"),
    ]
    links = []
    for path, label in items:
        cls = ' class="active"' if path == active else ""
        links.append(f'<a href="/admin/{path}"{cls}>{label}</a>')
    return f'<div class="navbar">{"&nbsp;".join(links)}</div>'


def _render_dashboard(cfg: EnterpriseConfig) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>母婴 Agent 管理后台</title>
<style>{_BASE_CSS}</style></head><body>
{_render_nav()}
<div class="container">
  <div class="card">
    <h1>仪表盘</h1>
    <div class="stat"><div class="stat-num">{cfg.enterprise_id}</div><div class="stat-label">企业 ID</div></div>
    <div class="stat"><div class="stat-num">{cfg.llm.kind}</div><div class="stat-label">LLM 模式</div></div>
    <div class="stat"><div class="stat-num">{cfg.embedding.kind}</div><div class="stat-label">嵌入模型</div></div>
  </div>
  <div class="card">
    <h2>快捷操作</h2>
    <p><a href="/admin/llm" class="btn">配置 LLM</a> &nbsp;
       <a href="/admin/database" class="btn">加载数据库</a> &nbsp;
       <a href="/admin/stores" class="btn">管理门店</a> &nbsp;
       <a href="/admin/gateway" class="btn">绑定微信</a> &nbsp;
       <a href="/admin/babies" class="btn">查看档案</a></p>
  </div>
</div>
</body></html>"""


def _render_llm_page(cfg: EnterpriseConfig) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>LLM 选择</title>
<style>{_BASE_CSS}</style></head><body>
{_render_nav("llm")}
<div class="container">
  <div class="card">
    <h1>LLM 配置</h1>
    <p style="color:#6b7280;margin-bottom:16px;">当前配置（修改后需重启 agent 生效）</p>
    <form id="llm-form">
      <div class="form-group">
        <label>Provider 类型</label>
        <select id="kind">
          <option value="mock" {'selected' if cfg.llm.kind=='mock' else ''}>Mock（测试）</option>
          <option value="ollama" {'selected' if cfg.llm.kind=='ollama' else ''}>Ollama（端侧本地）</option>
          <option value="cloud" {'selected' if cfg.llm.kind=='cloud' else ''}>Cloud（云 API）</option>
        </select>
      </div>
      <div class="form-group"><label>模型名称</label>
        <input id="model" value="{cfg.llm.model}"></div>
      <div class="form-group"><label>Base URL</label>
        <input id="base_url" value="{cfg.llm.base_url or ''}"></div>
      <div class="form-group"><label>API Key</label>
        <input id="api_key" type="password" value="{cfg.llm.api_key or ''}"
               placeholder="{'<已设置>' if cfg.llm.api_key else '未设置'}"></div>
      <div class="form-group"><label>Temperature</label>
        <input id="temperature" type="number" step="0.1" value="{cfg.llm.temperature}"></div>
      <div class="form-group"><label>Max Tokens</label>
        <input id="max_tokens" type="number" value="{cfg.llm.max_tokens}"></div>
      <button type="submit" class="btn">保存配置</button>
    </form>
    <div id="result" style="margin-top:12px;"></div>
  </div>
</div>
<script>
document.getElementById('llm-form').addEventListener('submit', async (e) => {{
  e.preventDefault();
  const body = {{
    kind: document.getElementById('kind').value,
    model: document.getElementById('model').value,
    base_url: document.getElementById('base_url').value,
    api_key: document.getElementById('api_key').value,
    temperature: parseFloat(document.getElementById('temperature').value),
    max_tokens: parseInt(document.getElementById('max_tokens').value),
  }};
  const r = await fetch('/api/llm', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(body)}});
  const data = await r.json();
  document.getElementById('result').innerHTML = '<div class="card" style="background:#d1fae5;">' + data.message + '</div>';
}});
</script>
</body></html>"""


def _render_database_page(cfg: EnterpriseConfig) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>数据库加载</title>
<style>{_BASE_CSS}</style></head><body>
{_render_nav("database")}
<div class="container">
  <div class="card">
    <h1>数据库加载</h1>
    <div id="status"></div>
  </div>
  <div class="card">
    <h2>触发 bundle 扫描加载</h2>
    <p style="color:#6b7280;margin-bottom:8px;">扫描收件箱目录中的 dataproc bundle 并加载到知识库</p>
    <button onclick="scanInbox()" class="btn">扫描并加载</button>
    <div id="scan-result" style="margin-top:12px;"></div>
  </div>
  <div class="card">
    <h2>待确认商品</h2>
    <div id="pending"></div>
  </div>
</div>
<script>
async function loadStatus() {{
  const r = await fetch('/api/database/status');
  const d = await r.json();
  document.getElementById('status').innerHTML = `
    <div class="stat"><div class="stat-num">${{d.corpus_count}}</div><div class="stat-label">语料条数</div></div>
    <div class="stat"><div class="stat-num">${{d.products_milk}}</div><div class="stat-label">奶粉商品</div></div>
    <div class="stat"><div class="stat-num">${{d.products_nutrition}}</div><div class="stat-label">营养品</div></div>
    <p style="margin-top:12px;">数据库路径: <code>${{d.db_path}}</code></p>
    <p>收件箱: <code>${{d.bundle_inbox_dir || '未配置'}}</code> ${{d.inbox_exists ? '✓' : '✗'}}</p>
  `;
}}
async function loadPending() {{
  const r = await fetch('/api/database/pending');
  const d = await r.json();
  if (!d.length) {{ document.getElementById('pending').innerHTML = '<p>无待确认商品</p>'; return; }}
  let html = '<table><tr><th>ID</th><th>名称</th><th>品牌</th><th>类型</th><th>操作</th></tr>';
  for (const p of d) {{
    html += `<tr><td>${{p.id}}</td><td>${{p.name}}</td><td>${{p.brand}}</td><td>${{p.table}}</td>
      <td><input id="val-${{p.id}}" placeholder="注册号/批准文号" style="width:160px;">
      <button onclick="confirmProduct(${{p.id}},'${{p.table}}')" class="btn">确认</button>
      <button onclick="deleteProduct(${{p.id}},'${{p.table}}')" class="btn btn-danger">删除</button></td></tr>`;
  }}
  html += '</table>';
  document.getElementById('pending').innerHTML = html;
}}
async function scanInbox() {{
  const r = await fetch('/api/database/scan', {{method:'POST'}});
  const d = await r.json();
  let html = `<div class="card" style="background:#d1fae5;">扫描完成</div>`;
  if (d.loaded) html += `<p>加载成功: ${{d.loaded.length}} 个 bundle</p>`;
  if (d.failed) html += `<p style="color:#dc2626;">失败: ${{d.failed.length}} 个</p>`;
  document.getElementById('scan-result').innerHTML = html;
  loadStatus(); loadPending();
}}
async function confirmProduct(id, table) {{
  const val = document.getElementById('val-'+id).value;
  await fetch(`/api/database/confirm?product_id=${{id}}&value=${{encodeURIComponent(val)}}&table=${{table}}`, {{method:'POST'}});
  loadPending();
}}
async function deleteProduct(id, table) {{
  if (!confirm('确认删除？')) return;
  await fetch(`/api/database/product?product_id=${{id}}&table=${{table}}`, {{method:'DELETE'}});
  loadPending();
}}
loadStatus(); loadPending();
</script>
</body></html>"""


def _render_stores_page(cfg: EnterpriseConfig) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>门店与员工管理</title>
<style>{_BASE_CSS}</style></head><body>
{_render_nav("stores")}
<div class="container">
  <div class="card">
    <h1>门店管理</h1>
    <div id="stores"></div>
  </div>
  <div class="card">
    <h2>新建门店</h2>
    <form id="store-form">
      <div class="form-group"><label>企业 ID</label><input id="s-eid" required></div>
      <div class="form-group"><label>企业名称</label><input id="s-name" required></div>
      <div class="form-group"><label>数据库路径</label><input id="s-db" value="instance.db"></div>
      <button type="submit" class="btn">创建</button>
    </form>
  </div>
  <div class="card">
    <h2>员工管理</h2>
    <div id="employees"></div>
  </div>
  <div class="card">
    <h2>添加员工</h2>
    <form id="emp-form">
      <div class="form-group"><label>企业 ID</label><input id="e-eid" value="{cfg.enterprise_id}" required></div>
      <div class="form-group"><label>员工 ID</label><input id="e-id" required></div>
      <div class="form-group"><label>员工姓名</label><input id="e-name" required></div>
      <button type="submit" class="btn">添加</button>
    </form>
  </div>
</div>
<script>
async function loadStores() {{
  const r = await fetch('/api/stores'); const d = await r.json();
  let html = '<table><tr><th>企业 ID</th><th>名称</th><th>数据库</th></tr>';
  for (const s of d) html += `<tr><td>${{s.enterprise_id}}</td><td>${{s.enterprise_name}}</td><td><code>${{s.db_path}}</code></td></tr>`;
  html += '</table>'; document.getElementById('stores').innerHTML = html;
}}
async function loadEmployees() {{
  const r = await fetch('/api/employees'); const d = await r.json();
  let html = '<table><tr><th>ID</th><th>企业</th><th>员工 ID</th><th>姓名</th><th>微信</th><th>操作</th></tr>';
  for (const e of d) html += `<tr><td>${{e.id}}</td><td>${{e.enterprise_id}}</td><td>${{e.employee_id}}</td><td>${{e.employee_name}}</td><td>${{e.wechat_name||'-'}}</td>
    <td><button onclick="delEmp(${{e.id}})" class="btn btn-danger">删除</button></td></tr>`;
  html += '</table>'; document.getElementById('employees').innerHTML = html;
}}
document.getElementById('store-form').addEventListener('submit', async (e) => {{
  e.preventDefault();
  await fetch('/api/stores', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{enterprise_id:document.getElementById('s-eid').value, enterprise_name:document.getElementById('s-name').value, db_path:document.getElementById('s-db').value}})}});
  loadStores();
}});
document.getElementById('emp-form').addEventListener('submit', async (e) => {{
  e.preventDefault();
  await fetch('/api/employees', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{enterprise_id:document.getElementById('e-eid').value, employee_id:document.getElementById('e-id').value, employee_name:document.getElementById('e-name').value}})}});
  loadEmployees();
}});
async function delEmp(id) {{ if(!confirm('确认删除？')) return; await fetch('/api/employees/'+id,{{method:'DELETE'}}); loadEmployees(); }}
loadStores(); loadEmployees();
</script>
</body></html>"""


def _render_gateway_page(cfg: EnterpriseConfig) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>微信网关绑定</title>
<style>{_BASE_CSS}</style></head><body>
{_render_nav("gateway")}
<div class="container">
  <div class="card">
    <h1>微信网关绑定</h1>
    <p style="color:#6b7280;margin-bottom:16px;">将员工的 iLink Bot Token 绑定到系统，实现微信消息接入</p>
    <div id="bindings"></div>
  </div>
  <div class="card">
    <h2>绑定微信网关</h2>
    <form id="bind-form">
      <div class="form-group"><label>企业 ID</label><input id="b-eid" value="{cfg.enterprise_id}" required></div>
      <div class="form-group"><label>员工 ID</label><input id="b-emp" required></div>
      <div class="form-group"><label>微信名称</label><input id="b-name" placeholder="如：门店小李"></div>
      <div class="form-group"><label>iLink Bot Token</label><input id="b-token" type="password" required></div>
      <button type="submit" class="btn">绑定</button>
    </form>
  </div>
</div>
<script>
async function loadBindings() {{
  const r = await fetch('/api/gateway'); const d = await r.json();
  let html = '<table><tr><th>ID</th><th>企业</th><th>员工</th><th>微信名</th><th>Token</th><th>操作</th></tr>';
  for (const b of d) html += `<tr><td>${{b.id}}</td><td>${{b.enterprise_id}}</td><td>${{b.employee_id}}</td><td>${{b.wechat_name||'-'}}</td><td><code>${{b.bot_token}}</code></td>
    <td><button onclick="unbind(${{b.id}})" class="btn btn-danger">解绑</button></td></tr>`;
  html += '</table>'; document.getElementById('bindings').innerHTML = html;
}}
document.getElementById('bind-form').addEventListener('submit', async (e) => {{
  e.preventDefault();
  await fetch('/api/gateway', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{enterprise_id:document.getElementById('b-eid').value, employee_id:document.getElementById('b-emp').value, wechat_name:document.getElementById('b-name').value, bot_token:document.getElementById('b-token').value}})}});
  loadBindings();
}});
async function unbind(id) {{ if(!confirm('确认解绑？')) return; await fetch('/api/gateway/'+id,{{method:'DELETE'}}); loadBindings(); }}
loadBindings();
</script>
</body></html>"""


def _render_babies_page(cfg: EnterpriseConfig) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>宝宝档案</title>
<style>{_BASE_CSS}</style></head><body>
{_render_nav("babies")}
<div class="container">
  <div class="card">
    <h1>宝宝档案查看</h1>
    <p style="color:#6b7280;margin-bottom:16px;">当前企业下的宝宝档案（只读概览）</p>
    <div id="babies"></div>
  </div>
  <div class="card" id="detail" style="display:none;">
    <h2>档案详情</h2>
    <div id="detail-content"></div>
  </div>
</div>
<script>
async function loadBabies() {{
  const r = await fetch('/api/babies'); const d = await r.json();
  let html = '<table><tr><th>ID</th><th>姓名</th><th>月龄</th><th>性别</th><th>阶段</th><th>状态</th><th>操作</th></tr>';
  for (const b of d) {{
    const badge = b.status === 'confirmed' ? 'badge-confirmed' : 'badge-pending';
    html += `<tr><td>${{b.baby_id}}</td><td>${{b.name}}</td><td>${{b.baby_age}}</td><td>${{b.gender}}</td><td>${{b.stage}}</td>
      <td><span class="badge ${{badge}}">${{b.status}}</span></td>
      <td><button onclick="viewDetail(${{b.baby_id}})" class="btn">查看</button></td></tr>`;
  }}
  html += '</table>'; document.getElementById('babies').innerHTML = html;
}}
async function viewDetail(id) {{
  const r = await fetch('/api/babies/'+id); const d = await r.json();
  let html = '<table>';
  for (const [k,v] of Object.entries(d)) {{
    let val = Array.isArray(v) ? v.join(', ') : (v === null ? '-' : v);
    html += `<tr><th>${{k}}</th><td>${{val}}</td></tr>`;
  }}
  html += '</table>';
  document.getElementById('detail-content').innerHTML = html;
  document.getElementById('detail').style.display = 'block';
}}
loadBabies();
</script>
</body></html>"""


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
