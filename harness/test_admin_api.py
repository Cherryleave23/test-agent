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
  A11 DELETE /api/employees/{id} 删除员工后 GET 不再返回
  A12 DELETE /api/gateway/{id} 解绑网关后 GET 不再返回
  A13 POST /api/database/confirm 确认 pending 商品
  A14 DELETE /api/database/product 删除商品
  A15 GET /api/babies/{id} 返回宝宝详情（不含敏感字段）
  A16 POST /api/database/scan 无收件箱时返回 400
  A17 GET /api/babies/{id} 不存在的 ID 返回 404
  A18 POST /api/database/confirm 非法表名返回 400

直接运行：python3 test_admin_api.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
import tempfile
import json
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from fastapi.testclient import TestClient
from common.config import EnterpriseConfig
from common.db import connect
from admin.server import create_app


# 临时目录管理：测试结束后统一清理
_temp_dirs: list = []


def _tmp_db() -> str:
    d = tempfile.mkdtemp()
    _temp_dirs.append(d)
    return os.path.join(d, "test.db")


def _make_client(db_path: str):
    cfg = EnterpriseConfig(
        enterprise_id="ent_test",
        enterprise_name="测试企业",
        db_path=db_path,
        baby_db_path=db_path,
    )
    app = create_app(cfg)
    return TestClient(app), cfg


def a1_app_routes():
    """A1: create_app 成功，路由包含 5 大板块。"""
    client, _ = _make_client(_tmp_db())
    routes = [r.path for r in client.app.routes]
    assert "/api/llm" in routes, "缺 LLM 配置 API"
    assert "/api/database/status" in routes, "缺数据库状态 API"
    assert "/api/stores" in routes, "缺门店 API"
    assert "/api/gateway" in routes, "缺微信网关 API"
    assert "/api/babies" in routes, "缺宝宝档案 API"


def a2_get_llm_config():
    """A2: GET /api/llm 返回当前 LLM 配置。"""
    client, _ = _make_client(_tmp_db())
    r = client.get("/api/llm")
    assert r.status_code == 200
    data = r.json()
    assert "kind" in data
    assert "model" in data
    assert "temperature" in data


def a3_post_llm_config():
    """A3: POST /api/llm 写入 yaml。"""
    db = _tmp_db()
    yaml_dir = tempfile.mkdtemp()
    _temp_dirs.append(yaml_dir)
    yaml_path = os.path.join(yaml_dir, "enterprise.yaml")
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
    client, _ = _make_client(_tmp_db())
    r = client.get("/api/database/status")
    assert r.status_code == 200
    data = r.json()
    assert "corpus_count" in data
    assert "products_milk" in data
    assert "db_path" in data


def a5_stores_empty():
    """A5: GET /api/stores 初始返回空列表。"""
    client, _ = _make_client(_tmp_db())
    r = client.get("/api/stores")
    assert r.status_code == 200
    assert r.json() == []


def a6_create_store():
    """A6: POST /api/stores 创建门店后 GET 能查到。"""
    client, _ = _make_client(_tmp_db())
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
    client, _ = _make_client(_tmp_db())
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
    client, _ = _make_client(_tmp_db())
    r = client.post("/api/gateway", json={
        "enterprise_id": "ent_test", "employee_id": "emp001",
        "wechat_name": "门店小李", "bot_token": "secret-token-12345678",
    })
    assert r.status_code == 200
    r2 = client.get("/api/gateway")
    bindings = r2.json()
    assert len(bindings) == 1
    assert bindings[0]["wechat_name"] == "门店小李"
    assert "secret-token-12345678" not in json.dumps(bindings), "bot_token 应被脱敏"
    assert "…" in bindings[0]["bot_token"], "token 应有脱敏省略号"


def a9_babies_list():
    """A9: GET /api/babies 返回宝宝档案列表。"""
    db = _tmp_db()
    client, _ = _make_client(db)
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
    # 列表不应包含敏感字段
    assert "allergens" not in babies[0], "列表不应返回 allergens"
    assert "medical_history" not in babies[0], "列表不应返回 medical_history"


def a10_dashboard_html():
    """A10: GET / 返回 HTML 仪表盘。"""
    client, _ = _make_client(_tmp_db())
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "仪表盘" in r.text


def a11_delete_employee():
    """A11: DELETE /api/employees/{id} 删除员工后 GET 不再返回。"""
    client, _ = _make_client(_tmp_db())
    client.post("/api/employees", json={
        "enterprise_id": "ent_test", "employee_id": "emp_del",
        "employee_name": "待删除",
    })
    r = client.get("/api/employees")
    emp_id = r.json()[0]["id"]
    r2 = client.delete(f"/api/employees/{emp_id}")
    assert r2.status_code == 200
    r3 = client.get("/api/employees")
    assert len(r3.json()) == 0, "删除后员工列表应为空"


def a12_unbind_gateway():
    """A12: DELETE /api/gateway/{id} 解绑网关后 GET 不再返回。"""
    client, _ = _make_client(_tmp_db())
    client.post("/api/gateway", json={
        "enterprise_id": "ent_test", "employee_id": "emp002",
        "wechat_name": "小李", "bot_token": "tok-12345678",
    })
    r = client.get("/api/gateway")
    bind_id = r.json()[0]["id"]
    r2 = client.delete(f"/api/gateway/{bind_id}")
    assert r2.status_code == 200
    r3 = client.get("/api/gateway")
    assert len(r3.json()) == 0, "解绑后网关列表应为空"


def _insert_test_product(db: str, name: str = "测试奶粉") -> int:
    """用 raw SQL 插入一条 pending 商品，返回 product_id。

    避免在测试中创建第二个 KnowledgeStore（两个 Chroma PersistentClient
    在同一目录会导致 'database disk image is malformed'）。
    """
    with connect(db) as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO products_milk(enterprise_id,name,brand,stage,age_range,price,
               origin,milk_origin,ptype,reg_number,manufacturer,ingredients,nutrition,highlights)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("ent_test", name, "测试品牌", "1段", "0-6个月",
             199, "中国", "新西兰", "牛奶粉", "", "测试厂商",
             "生牛乳", "蛋白质", "优质蛋白"),
        )
        pid = cur.lastrowid
        conn.commit()
    return pid


def a13_confirm_product():
    """A13: POST /api/database/confirm 确认 pending 商品。"""
    db = _tmp_db()
    client, _ = _make_client(db)
    # 触发 schema 初始化（GET /api/database/status 会调用 _get_store()）
    client.get("/api/database/status")
    # 用 raw SQL 插入 pending 商品（避免创建第二个 KnowledgeStore 导致 Chroma 冲突）
    pid = _insert_test_product(db, "测试奶粉")
    r = client.post(f"/api/database/confirm?product_id={pid}&value=REG123&table=products_milk")
    assert r.status_code == 200


def a14_delete_product():
    """A14: DELETE /api/database/product 删除商品。"""
    db = _tmp_db()
    client, _ = _make_client(db)
    # 触发 schema 初始化
    client.get("/api/database/status")
    # 用 raw SQL 插入商品
    pid = _insert_test_product(db, "待删奶粉")
    r = client.delete(f"/api/database/product?product_id={pid}&table=products_milk")
    assert r.status_code == 200


def a15_baby_detail_no_sensitive():
    """A15: GET /api/babies/{id} 返回详情（不含 allergens 等敏感字段）。"""
    db = _tmp_db()
    client, _ = _make_client(db)
    from baby.store import BabyProfileStore
    from baby.models import BabyProfile
    store = BabyProfileStore(db)
    cid = store.get_or_create_customer("ent_test", "emp001", "张姐")
    bid = store.create_baby(BabyProfile(
        baby_id=None, enterprise_id="ent_test", employee_id="emp001",
        customer_id=cid, name="妞妞", baby_age="6个月", gender="女",
        stage="婴儿", allergens=["牛奶蛋白"], budget=1200,
        brand_preference=["A2"], category="配方粉", health_notes="湿疹",
        birth_date="2024-01-15", gestational_weeks=40,
        medical_history=["湿疹"], feeding_history=["母乳"],
    ))
    r = client.get(f"/api/babies/{bid}")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "妞妞"
    # 不应返回敏感健康详情
    assert "allergens" not in data, "详情不应返回 allergens"
    assert "medical_history" not in data, "详情不应返回 medical_history"
    assert "feeding_history" not in data, "详情不应返回 feeding_history"
    assert "health_notes" not in data, "详情不应返回 health_notes"


def a16_scan_no_inbox():
    """A16: POST /api/database/scan 无收件箱时返回 400。"""
    client, _ = _make_client(_tmp_db())
    os.environ.pop("BUNDLE_INBOX_DIR", None)
    r = client.post("/api/database/scan")
    assert r.status_code == 400, f"无收件箱应返回 400，实际: {r.status_code}"


def a17_baby_not_found():
    """A17: GET /api/babies/{id} 不存在的 ID 返回 404。"""
    client, _ = _make_client(_tmp_db())
    r = client.get("/api/babies/99999")
    assert r.status_code == 404, f"不存在的 ID 应返回 404，实际: {r.status_code}"


def a18_confirm_bad_table():
    """A18: POST /api/database/confirm 非法表名返回 400。"""
    client, _ = _make_client(_tmp_db())
    r = client.post("/api/database/confirm?product_id=1&value=x&table=evil_table")
    assert r.status_code == 400, f"非法表名应返回 400，实际: {r.status_code}"


CHECKS = [
    ("A1 create_app 路由含 5 大板块", a1_app_routes),
    ("A2 GET /api/llm 返回 LLM 配置", a2_get_llm_config),
    ("A3 POST /api/llm 写入 yaml", a3_post_llm_config),
    ("A4 GET /api/database/status 知识库统计", a4_database_status),
    ("A5 GET /api/stores 初始空列表", a5_stores_empty),
    ("A6 POST /api/stores 创建门店", a6_create_store),
    ("A7 POST /api/employees 创建员工", a7_create_employee),
    ("A8 POST /api/gateway 绑定微信（token脱敏）", a8_gateway_binding),
    ("A9 GET /api/babies 宝宝档案列表（无敏感字段）", a9_babies_list),
    ("A10 GET / 仪表盘 HTML", a10_dashboard_html),
    ("A11 DELETE /api/employees/{id} 删除员工", a11_delete_employee),
    ("A12 DELETE /api/gateway/{id} 解绑网关", a12_unbind_gateway),
    ("A13 POST /api/database/confirm 确认商品", a13_confirm_product),
    ("A14 DELETE /api/database/product 删除商品", a14_delete_product),
    ("A15 GET /api/babies/{id} 详情无敏感字段", a15_baby_detail_no_sensitive),
    ("A16 POST /api/database/scan 无收件箱返回 400", a16_scan_no_inbox),
    ("A17 GET /api/babies/{id} 不存在返回 404", a17_baby_not_found),
    ("A18 POST /api/database/confirm 非法表名返回 400", a18_confirm_bad_table),
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
    # 清理临时目录
    for d in _temp_dirs:
        shutil.rmtree(d, ignore_errors=True)
    print(f"=== Summary: {len(CHECKS) - len(failed)}/{len(CHECKS)} passed ===")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("RESULT: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
