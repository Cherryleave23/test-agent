"""验证时序+焦点+消歧场景。"""
import sys, os
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

from session.constraints import extract_constraints, UserConstraints
from baby.resolution import (
    focus_is_stable, _rule_extract, _validate_extracted,
    _BABY_SIGNALS, _extract_birth_date, _extract_medical_history,
)

known = [{"baby_id": 1, "baby_name": "小李", "customer_name": "王芳", "customer_id": 1}]
focus = 1

msgs = [
    "小李1年前3岁，3个月前吃3段奶粉，昨天吃了一点冰淇淋",
    "现在肚子疼",
]

for msg in msgs:
    print(f"消息: '{msg}'")
    print(f"  _BABY_SIGNALS: {bool(_BABY_SIGNALS.search(msg))}")
    print(f"  focus_is_stable: {focus_is_stable(known, focus, msg)}")
    ext = _rule_extract(msg)
    print(f"  _rule_extract: baby_age='{ext.baby_age}' stage='{ext.stage}' medical={ext.medical_history} birth_date='{ext.birth_date}'")
    print(f"  is_empty: {ext.is_empty_attr()}")
    uc = extract_constraints(msg)
    print(f"  extract_constraints: baby_age='{uc.baby_age}' stage='{uc.stage}'")
    print()
