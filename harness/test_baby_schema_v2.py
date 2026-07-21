#!/usr/bin/env python3
# @module baby
"""P2-v2 档案 schema 扩展验收（MOD-baby-profile）。

验证新增字段的持久化、合并、迁移和 prompt 注入：
  P24: birth_date / gestational_weeks 字段 round-trip
  P25: medical_history / feeding_history 列表 round-trip + merge 去重
  P26: SQL 迁移（旧库 ALTER TABLE 自动加列）
  P27: to_prompt_block() 含新字段

直接运行：python3 test_baby_schema_v2.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from baby.models import BabyProfile, Customer  # noqa: E402
from baby.store import BabyProfileStore  # noqa: E402
from common.db import connect  # noqa: E402


def _tmp_db():
    return os.path.join(tempfile.mkdtemp(), "baby_v2.db")


# ---------------------------------------------------------------------------
# P24 birth_date / gestational_weeks round-trip
# ---------------------------------------------------------------------------
def _p24_birth_gestational_roundtrip():
    store = BabyProfileStore(_tmp_db())
    cid = store.get_or_create_customer("ent1", "emp1", "张姐")
    baby = BabyProfile(
        baby_id=None, enterprise_id="ent1", employee_id="emp1",
        customer_id=cid, name="共青二宝",
        baby_age="14个月", stage="3段",
        birth_date="2025-05-21",
        gestational_weeks=35,
        status="confirmed",
    )
    bid = store.create_baby(baby)
    got = store.get_baby(bid)
    assert got is not None
    assert got.birth_date == "2025-05-21", f"birth_date 不匹配：{got.birth_date}"
    assert got.gestational_weeks == 35, f"gestational_weeks 不匹配：{got.gestational_weeks}"


# ---------------------------------------------------------------------------
# P25 medical_history / feeding_history round-trip + merge 去重
# ---------------------------------------------------------------------------
def _p25_medical_feeding_roundtrip():
    store = BabyProfileStore(_tmp_db())
    cid = store.get_or_create_customer("ent1", "emp1", "张姐")
    baby = BabyProfile(
        baby_id=None, enterprise_id="ent1", employee_id="emp1",
        customer_id=cid, name="共青二宝",
        medical_history=["早产35周", "出生5.18斤", "新生儿科9天"],
        feeding_history=["混合喂养→纯奶粉", "合生元派星3段"],
        status="confirmed",
    )
    bid = store.create_baby(baby)
    got = store.get_baby(bid)
    assert got is not None
    assert got.medical_history == ["早产35周", "出生5.18斤", "新生儿科9天"], \
        f"medical_history 不匹配：{got.medical_history}"
    assert got.feeding_history == ["混合喂养→纯奶粉", "合生元派星3段"], \
        f"feeding_history 不匹配：{got.feeding_history}"


def _p25b_merge_dedup_lists():
    """merge 时 medical_history / feeding_history 去重保序"""
    base = BabyProfile(
        baby_id=1, enterprise_id="e", employee_id="m", customer_id=1, name="A",
        medical_history=["早产35周", "出生5.18斤"],
        feeding_history=["混合喂养→纯奶粉"],
    )
    new = BabyProfile(
        baby_id=None, enterprise_id="e", employee_id="m", customer_id=1, name="A",
        medical_history=["早产35周", "新生儿科9天"],  # 重复 + 新增
        feeding_history=["合生元派星3段", "混合喂养→纯奶粉"],  # 新增 + 重复
    )
    merged = base.merge(new)
    assert merged.medical_history == ["早产35周", "出生5.18斤", "新生儿科9天"], \
        f"medical_history merge 去重失败：{merged.medical_history}"
    assert merged.feeding_history == ["混合喂养→纯奶粉", "合生元派星3段"], \
        f"feeding_history merge 去重失败：{merged.feeding_history}"


# ---------------------------------------------------------------------------
# P26 SQL 迁移：旧库 ALTER TABLE 自动加列
# ---------------------------------------------------------------------------
def _p26_sql_migration():
    """旧库（无新列）打开后自动迁移"""
    db_path = _tmp_db()
    # 1) 先建一个"旧版"表（无新列）
    with connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE customers (
                customer_id INTEGER PRIMARY KEY,
                enterprise_id TEXT NOT NULL,
                employee_id TEXT NOT NULL,
                name TEXT NOT NULL,
                phone TEXT, notes TEXT, created_at REAL
            );
            CREATE TABLE babies (
                baby_id INTEGER PRIMARY KEY,
                enterprise_id TEXT NOT NULL,
                employee_id TEXT NOT NULL,
                customer_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                baby_age TEXT, gender TEXT, stage TEXT,
                allergens_json TEXT, budget REAL,
                brand_preference_json TEXT, category TEXT, health_notes TEXT,
                status TEXT DEFAULT 'pending',
                created_at REAL, updated_at REAL,
                FOREIGN KEY(customer_id) REFERENCES customers(customer_id)
            );
        """)
        conn.commit()
    # 确认旧表无新列
    with connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(babies)").fetchall()}
    assert "birth_date" not in cols, "前置条件：旧库不应有 birth_date"
    # 2) 用 BabyProfileStore 打开 → 触发迁移
    store = BabyProfileStore(db_path)
    # 3) 验证新列已添加
    with connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(babies)").fetchall()}
    assert "birth_date" in cols, "迁移后应有 birth_date"
    assert "gestational_weeks" in cols, "迁移后应有 gestational_weeks"
    assert "medical_history_json" in cols, "迁移后应有 medical_history_json"
    assert "feeding_history_json" in cols, "迁移后应有 feeding_history_json"
    # 4) 验证旧库迁移后能正常写入新字段
    cid = store.get_or_create_customer("ent1", "emp1", "张姐")
    bid = store.create_baby(BabyProfile(
        None, "ent1", "emp1", cid, "测试宝",
        birth_date="2025-01-01", gestational_weeks=38,
        medical_history=["黄疸"],
    ))
    got = store.get_baby(bid)
    assert got.birth_date == "2025-01-01"
    assert got.gestational_weeks == 38
    assert got.medical_history == ["黄疸"]


# ---------------------------------------------------------------------------
# P27 to_prompt_block() 含新字段
# ---------------------------------------------------------------------------
def _p27_prompt_block_new_fields():
    baby = BabyProfile(
        baby_id=1, enterprise_id="e", employee_id="m", customer_id=1, name="共青二宝",
        baby_age="14个月", stage="3段",
        birth_date="2025-05-21",
        gestational_weeks=35,
        medical_history=["早产35周", "出生5.18斤"],
        feeding_history=["混合喂养→纯奶粉", "合生元派星3段"],
        status="confirmed",
    )
    block = baby.to_prompt_block("张姐")
    assert "出生日期：2025-05-21" in block, f"block 缺 birth_date：\n{block}"
    assert "孕周：35（早产）" in block, f"block 缺 gestational_weeks 早产标记：\n{block}"
    assert "医疗史" in block and "早产35周" in block, f"block 缺 medical_history：\n{block}"
    assert "喂养史" in block and "合生元派星3段" in block, f"block 缺 feeding_history：\n{block}"


def _p27b_prompt_block_term_not_preterm():
    """孕周>=37 不标早产"""
    baby = BabyProfile(
        baby_id=1, enterprise_id="e", employee_id="m", customer_id=1, name="足月宝",
        gestational_weeks=40, status="confirmed",
    )
    block = baby.to_prompt_block()
    assert "孕周：40" in block
    assert "早产" not in block, "40周不应标早产"


def _p27c_prompt_block_health_notes_fallback():
    """无结构化字段时 health_notes 作为兜底展示"""
    baby = BabyProfile(
        baby_id=1, enterprise_id="e", employee_id="m", customer_id=1, name="旧宝",
        health_notes="早产35周 旧格式备注",
        status="confirmed",
    )
    block = baby.to_prompt_block()
    assert "健康/喂养备注：早产35周 旧格式备注" in block
    assert "医疗史" not in block  # 无结构化字段时不展示


# ---------------------------------------------------------------------------
# P27d upsert 融合新字段
# ---------------------------------------------------------------------------
def _p27d_upsert_merges_new_fields():
    store = BabyProfileStore(_tmp_db())
    cid = store.get_or_create_customer("ent1", "emp1", "张姐")
    bid = store.create_baby(BabyProfile(
        None, "ent1", "emp1", cid, "共青二宝",
        baby_age="14个月", status="pending",
    ))
    # 第一次 upsert：补 birth_date + gestational_weeks
    store.upsert_baby_attrs(bid, BabyProfile(
        None, "ent1", "emp1", cid, "共青二宝",
        birth_date="2025-05-21", gestational_weeks=35,
    ))
    # 第二次 upsert：补 medical_history
    store.upsert_baby_attrs(bid, BabyProfile(
        None, "ent1", "emp1", cid, "共青二宝",
        medical_history=["早产35周", "出生5.18斤"],
    ))
    got = store.get_baby(bid)
    assert got.birth_date == "2025-05-21", "跨轮 upsert 应保留 birth_date"
    assert got.gestational_weeks == 35, "跨轮 upsert 应保留 gestational_weeks"
    assert got.medical_history == ["早产35周", "出生5.18斤"]
    assert got.baby_age == "14个月", "跨轮 upsert 应保留原始 baby_age"


CHECKS = [
    ("P24 birth_date/gestational_weeks round-trip", _p24_birth_gestational_roundtrip),
    ("P25 medical/feeding round-trip", _p25_medical_feeding_roundtrip),
    ("P25b merge 去重保序", _p25b_merge_dedup_lists),
    ("P26 SQL 迁移自动加列", _p26_sql_migration),
    ("P27 to_prompt_block 含新字段", _p27_prompt_block_new_fields),
    ("P27b 足月不标早产", _p27b_prompt_block_term_not_preterm),
    ("P27c health_notes 兜底展示", _p27c_prompt_block_health_notes_fallback),
    ("P27d upsert 跨轮融合新字段", _p27d_upsert_merges_new_fields),
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
        except Exception as e:  # noqa: BLE001
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
