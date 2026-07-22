#!/usr/bin/env python3
# @module admin
"""WebUI 管理后台 API 验收（MOD-admin）。

  A1  create_app 成功创建 FastAPI 实例，路由包含 5 大板块
  A2  GET /api/llm 返回当前 LLM 配置
  A3  POST /api/llm 写入 yaml（临时文件验证，不污染真实配置）
  A4  GET /api/database/status 返回知识库统计
  A5  GET /api/stores 返回门店列表（初始空）
  A6  POST /api/stores 创建门店后 GET 能查到
  A7  POST /api/employees 创建员工后 GET 能查到
  A8  POST /api/gateway 绑定微信网关后 GET 能查到（token 脱敏）
  A9  GET /api/babies 返回宝宝档案列表（只读）
  A10 GET / 页面返回 HTML（仪表盘）

直接运行：python3 test_admin_api.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
import tempfile
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from fastapi.testclient import TestClient
from common.config import EnterpriseConfig
from common.db import connect
from admin.server import create_app


def _make_cfg(db_path: str) -> EnterpriseConfig:
    return EnterpriseConfig(
        enterprise_id="ent_test",
        enterprise_name="测试企业",
        db_path=db_path,
        baby_db_path=db_path,
    )


def _make_client(db_path: str):
    cfg = _make_cfg(db_path)
    app = create_app(cfg)
    return TestClient(app), cfg


def a1_app_routes():
    """A1: create_app 成功，路由包含 5 大板块。"""
    db = os.path.join(tempfile.mkdtemp(), "test.db")
    client, _ = _make_client(db)
    routes = [r.path for r in client.app.routes]
    assert "/api/llm" in routes, "缺 LLM 配置 API"
    assert "/api/database/status" in routes, "缺数据库状态 API"
    assert "/api/stores" in routes, "缺门店 API"
    assert "/api/gateway" in routes, "缺微信网关 API"
    assert "/api/babies" in routes, "缺宝宝档案 API"


def a2_get_llm_config():
    """A2: GET /api/llm 返回当前 LLM 配置。"""
    db = os.path.join(tempfile.mkdtemp(), "test.db")
    client, _ = _make_client(db)
    r = client.get("/api/llm")
    assert r.status_code == 200
    data = r.json()
    assert "kind" in data
    assert "model" in data
    assert "temperature" in data


def a3_post_llm_config():
    """A3: POST /api/llm 写入 yaml。"""
    db = os.path.join(tempfile.mkdtemp(), "test.db")
    # 用临时 yaml 文件
    yaml_path = os.path.join(tempfile.mkdtemp(), "enterprise.yaml")
    os.environ["AGENT_CONFIG_PATH"] = yaml_path
    try:
        client, _ = _make_client(db)
        r = client.post("/api/llm", json={
            "kind": "ollama", "model": "qwen2.5:7b",
            "base_url": "http://localhost:11434",
            "api_key": "", "temperature": 0.3, "max_tokens": 2048,
        })
        assert r.status_code == 200
        assert os.path.isfile(yaml_path), "yaml 文件应被创建"
        import yaml
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        assert data["llm"]["kind"] == "ollama"
        assert data["llm"]["model"] == "qwen2.5:7b"
    finally:
        os.environ.pop("AGENT_CONFIG_PATH", None)


def a4_database_status():
    """A4: GET /api/database/status 返回知识库统计。"""
    db = os.path.join(tempfile.mkdtemp(), "test.db")
    client, _ = _make_client(db)
    r = client.get("/api/database/status")
    assert r.status_code == 200
    data = r.json()
    assert "corpus_count" in data
    assert "products_milk" in data
    assert "db_path" in data


def a5_stores_empty():
    """A5: GET /api/stores 初始返回空列表。"""
    db = os.path.join(tempfile.mkdtemp(), "test.db")
    client, _ = _make_client(db)
    r = client.get("/api/stores")
    assert r.status_code == 200
    assert r.json() == []


def a6_create_store():
    """A6: POST /api/stores 创建门店后 GET 能查到。"""
    db = os.path.join(tempfile.mkdtemp(), "test.db")
    client, _ = _make_client(db)
    r = client.post("/api/stores", json={
        "enterprise_id": "ent_new", "enterprise_name": "新门店",
        "db_path": "new_store.db",
    })
    assert r.status_code == 200
    r2 = client.get("/api/stores")
    stores = r2.json()
    assert len(stores) == 1
    assert stores[0]["enterprise_id"] == "ent_new"
    assert stores[0]["enterprise_name"] == "新门店"


def a7_create_employee():
    """A7: POST /api/employees 创建员工后 GET 能查到。"""
    db = os.path.join(tempfile.mkdtemp(), "test.db")
    client, _ = _make_client(db)
    client.post("/api/employees", json={
        "enterprise_id": "ent_test", "employee_id": "emp001",
        "employee_name": "张三",
    })
    r = client.get("/api/employees")
    emps = r.json()
    assert len(emps) == 1
    assert emps[0]["employee_id"] == "emp001"
    assert emps[0]["employee_name"] == "张三"


def a8_gateway_binding():
    """A8: POST /api/gateway 绑定微信网关后 GET 能查到（token 脱敏）。"""
    db = os.path.join(tempfile.mkdtemp(), "test.db")
    client, _ = _make_client(db)
    r = client.post("/api/gateway", json={
        "enterprise_id": "ent_test", "employee_id": "emp001",
        "wechat_name": "门店小李", "bot_token": "secret-token-12345678",
    })
    assert r.status_code == 200
    r2 = client.get("/api/gateway")
    bindings = r2.json()
    assert len(bindings) == 1
    assert bindings[0]["wechat_name"] == "门店小李"
    # token 应被脱敏
    assert "secret-token-12345678" not in json.dumps(bindings), "bot_token 应被脱敏"
    assert "…" in bindings[0]["bot_token"], "token 应有脱敏省略号"


def a9_babies_list():
    """A9: GET /api/babies 返回宝宝档案列表。"""
    db = os.path.join(tempfile.mkdtemp(), "test.db")
    client, _ = _make_client(db)
    # 先创建一个宝宝档案
    from baby.store import BabyProfileStore
    from baby.models import BabyProfile
    store = BabyProfileStore(db)
    cid = store.get_or_create_customer("ent_test", "emp001", "张姐")
    store.create_baby(BabyProfile(
        baby_id=None, enterprise_id="ent_test", employee_id="emp001",
        customer_id=cid, name="妞妞", baby_age="6个月", gender="女",
        stage="婴儿", allergens=["牛奶蛋白"], budget=1200,
        brand_preference=["A2"], category="配方粉", health_notes="湿疹",
        birth_date="2024-01-15", gestational_weeks=40,
        medical_history=["无"], feeding_history=["母乳"],
    ))
    r = client.get("/api/babies")
    assert r.status_code == 200
    babies = r.json()
    assert len(babies) >= 1
    assert babies[0]["name"] == "妞妞"
    assert babies[0]["baby_age"] == "6个月"


def a10_dashboard_html():
    """A10: GET / 返回 HTML 仪表盘。"""
    db = os.path.join(tempfile.mkdtemp(), "test.db")
    client, _ = _make_client(db)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "仪表盘" in r.text


CHECKS = [
    ("A1 create_app 路由含 5 大板块", a1_app_routes),
    ("A2 GET /api/llm 返回 LLM 配置", a2_get_llm_config),
    ("A3 POST /api/llm 写入 yaml", a3_post_llm_config),
    ("A4 GET /api/database/status 知识库统计", a4_database_status),
    ("A5 GET /api/stores 初始空列表", a5_stores_empty),
    ("A6 POST /api/stores 创建门店", a6_create_store),
    ("A7 POST /api/employees 创建员工", a7_create_employee),
    ("A8 POST /api/gateway 绑定微信（token脱敏）", a8_gateway_binding),
    ("A9 GET /api/babies 宝宝档案列表", a9_babies_list),
    ("A10 GET / 仪表盘 HTML", a10_dashboard_html),
]


def main():
    failed = []
    for name, fn in CHECKS:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
        except Exception as e:
            print(f"[FAIL] {name}: 异常 {type(e).__name__}: {e}")
            failed.append(name)
    print(f"=== Summary: {len(CHECKS) - len(failed)}/{len(CHECKS)} passed ===")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("RESULT: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
