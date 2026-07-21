#!/usr/bin/env python3
# @module deploy
"""健康数据加密（MOD-deploy P0-3）真实运行验收 harness。

按 CVC：真实落库 + 真实加解密，断言 PASS/FAIL，非自述。
覆盖：
  E1 落库密文 ≠ 明文（静态加密生效）
  E2 round-trip 还原一致
  E3 list_for_employee 返回解密值（LLM 消歧上下文需明文但不落明文）
  E4 明文 name 仍可被实体链接命中（加密不影响消歧）
  E5 生产/部署模式缺密钥启动抛错（禁止静默明文落库）
  E6 惰性迁移：升级前明文行（无 fernet: 前缀）读取不崩、原样返回

直接运行：python3 test_data_encryption.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
import tempfile
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cryptography.fernet import Fernet  # noqa: E402

from baby.models import BabyProfile  # noqa: E402
from baby.store import BabyProfileStore  # noqa: E402
from common.crypto import get_vault, reset_vault, Vault, KeyMissing  # noqa: E402
from common.db import connect  # noqa: E402

# 用真实 Fernet key（base64）驱动本 harness，避免命中 dev key 路径。
os.environ["AGENT_DATA_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
reset_vault()


def _tmp_db():
    return os.path.join(tempfile.mkdtemp(), "baby.db")


def _raw_col(db_path, baby_id, col):
    with connect(db_path) as conn:
        return conn.execute(
            f"SELECT {col} FROM babies WHERE baby_id=?", (baby_id,)
        ).fetchone()[col]


def _make_baby(ent="e1", emp="emp1", name="妞妞", **kw):
    base = dict(
        baby_id=None,
        enterprise_id=ent, employee_id=emp, customer_id=1, name=name,
        baby_age="6个月", gender="女", stage="婴儿",
        allergens=["牛奶蛋白", "鸡蛋"], budget=1200.5,
        brand_preference=["A2", "飞鹤"], category="配方粉",
        health_notes="湿疹", birth_date="2024-01-15", gestational_weeks=40,
        medical_history=["无"], feeding_history=["母乳"],
    )
    base.update(kw)
    return BabyProfile(**base)


def e1_ciphertext_not_plaintext():
    db = _tmp_db()
    store = BabyProfileStore(db)
    cid = store.get_or_create_customer("e1", "emp1", "张姐")
    bid = store.create_baby(_make_baby(customer_id=cid))
    raw_age = _raw_col(db, bid, "baby_age")
    assert raw_age.startswith("fernet:"), f"baby_age 应加密存储，实际：{raw_age!r}"
    assert raw_age != "6个月", "密文不应等于明文"
    raw_allergens = _raw_col(db, bid, "allergens_json")
    assert raw_allergens.startswith("fernet:"), "allergens_json 应加密"
    assert "牛奶蛋白" not in raw_allergens, "密文中不得出现明文敏感数据"
    # 明文查询字段保持明文
    raw_name = _raw_col(db, bid, "name")
    assert raw_name == "妞妞", "name 应保持明文（实体链接必需）"


def e2_roundtrip():
    db = _tmp_db()
    store = BabyProfileStore(db)
    cid = store.get_or_create_customer("e1", "emp1", "张姐")
    bid = store.create_baby(_make_baby(customer_id=cid))
    b = store.get_baby(bid)
    assert b.baby_age == "6个月", b.baby_age
    assert b.allergens == ["牛奶蛋白", "鸡蛋"], b.allergens
    assert b.budget == 1200.5, b.budget
    assert b.birth_date == "2024-01-15", b.birth_date
    assert b.gestational_weeks == 40, b.gestational_weeks
    assert b.medical_history == ["无"], b.medical_history
    # 主动归档路径也需解密正确
    store.upsert_baby_attrs(bid, BabyProfile(baby_id=None, enterprise_id="", employee_id="",
                                             customer_id=0, name="", baby_age="8个月",
                                             allergens=["花生"]))
    b2 = store.get_baby(bid)
    assert b2.baby_age == "8个月", b2.baby_age
    assert "花生" in b2.allergens, b2.allergens


def e3_list_decrypted():
    db = _tmp_db()
    store = BabyProfileStore(db)
    cid = store.get_or_create_customer("e1", "emp1", "张姐")
    store.create_baby(_make_baby(customer_id=cid))
    items = store.list_for_employee("e1", "emp1")
    assert len(items) == 1, items
    it = items[0]
    assert it["baby_age"] == "6个月", it
    assert it["allergens"] == ["牛奶蛋白", "鸡蛋"], it
    assert it["budget"] == 1200.5, it
    assert it["category"] == "配方粉", it


def e4_name_plaintext_resolves():
    db = _tmp_db()
    store = BabyProfileStore(db)
    cid = store.get_or_create_customer("e1", "emp1", "张姐")
    bid = store.create_baby(_make_baby(customer_id=cid))
    store.mark_confirmed(bid)
    # 实体链接靠明文 name 查找——加密不应破坏
    found = store.find_baby_by_name("e1", "emp1", "妞妞")
    assert found == bid, f"明文 name 应可被实体链接命中，found={found}"


def e5_prod_missing_key_raises():
    # 生产/部署模式要求密钥；缺密钥必须抛错，禁止静默明文落库
    reset_vault()
    saved = os.environ.pop("AGENT_DATA_ENCRYPTION_KEY", None)
    try:
        raised = False
        try:
            Vault.from_env(require=True)
        except KeyMissing:
            raised = True
        assert raised, "生产模式缺 AGENT_DATA_ENCRYPTION_KEY 应抛 KeyMissing"
    finally:
        if saved is not None:
            os.environ["AGENT_DATA_ENCRYPTION_KEY"] = saved
        reset_vault()


def e6_legacy_plaintext_compat():
    # 升级前的明文行（无 fernet: 前缀）应能读取且不崩（惰性迁移兼容）
    db = _tmp_db()
    store = BabyProfileStore(db)
    cid = store.get_or_create_customer("e1", "emp1", "张姐")
    bid = store.create_baby(_make_baby(customer_id=cid))  # 先建一个，拿到 schema
    # 模拟旧库：直接写入一条无前缀明文 baby_age 的行
    with connect(db) as conn:
        cur = conn.execute(
            """INSERT INTO babies(enterprise_id, employee_id, customer_id, name,
               baby_age, status, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            ("e1", "emp1", cid, "壮壮", "2岁", "confirmed", 0.0, 0.0),
        )
        legacy_id = cur.lastrowid
        conn.commit()
    b = store.get_baby(legacy_id)
    assert b.baby_age == "2岁", f"旧库明文行应原样读取，实际：{b.baby_age!r}"
    # 再次归档后变为加密，但值不变
    store.upsert_baby_attrs(legacy_id, BabyProfile(baby_id=None, enterprise_id="",
                                                   employee_id="", customer_id=0, name="",
                                                   baby_age="3岁"))
    b2 = store.get_baby(legacy_id)
    assert b2.baby_age == "3岁", b2.baby_age
    raw = _raw_col(db, legacy_id, "baby_age")
    assert raw.startswith("fernet:"), "归档后明文行应被改写为加密"


CHECKS = [
    ("E1 落库密文≠明文(静态加密生效)", e1_ciphertext_not_plaintext),
    ("E2 round-trip 还原一致", e2_roundtrip),
    ("E3 list_for_employee 返回解密值", e3_list_decrypted),
    ("E4 明文name仍可被实体链接命中", e4_name_plaintext_resolves),
    ("E5 生产缺密钥启动抛错", e5_prod_missing_key_raises),
    ("E6 惰性迁移(旧明文行兼容)", e6_legacy_plaintext_compat),
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
