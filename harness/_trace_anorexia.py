"""验证上下文连续场景：聊宝宝感冒 → 厌食。"""
import sys, os
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

from session.constraints import extract_constraints
from baby.resolution import (
    focus_is_stable, _rule_extract, _BABY_SIGNALS, _PRONOUN_SIGNALS,
    _MEDICAL_HISTORY_KEYWORDS, _FEEDING_HISTORY_KEYWORDS,
)

known = [{"baby_id": 1, "baby_name": "小宝", "customer_name": "王芳", "customer_id": 1}]
focus_baby_id = 1

msgs = ["最近有点厌食", "最近有点感冒", "他食欲不好", "有点拉肚子", "最近不爱吃奶了"]

for msg in msgs:
    print(f"消息: '{msg}'")
    print(f"  _BABY_SIGNALS命中: {bool(_BABY_SIGNALS.search(msg))}")
    print(f"  _PRONOUN_SIGNALS命中: {bool(_PRONOUN_SIGNALS.search(msg))}")
    print(f"  focus_is_stable: {focus_is_stable(known, focus_baby_id, msg)}")
    ext = _rule_extract(msg)
    print(f"  _rule_extract: medical={ext.medical_history}, feeding={ext.feeding_history}, empty={ext.is_empty_attr()}")
    uc = extract_constraints(msg)
    print(f"  extract_constraints: baby_age='{uc.baby_age}'")
    print()
