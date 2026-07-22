"""诊断脚本2：追踪 _match_known 的行为。"""
import os, sys, random, asyncio, tempfile, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "harness"))
from baby.models import BabyProfile
from baby.store import BabyProfileStore
from baby.archive import resolve_and_archive
from baby.resolution import _match_known, _norm
from test_ultimate_baby_harness import (
    BABY_GROUND_TRUTHS, SHATTERED_INFO, SEED,
    DisambigMockProvider,
)

async def main():
    store = BabyProfileStore(os.path.join(tempfile.mkdtemp(), "diag2.db"))
    ent, emp = "ent_ultimate", "emp_ultimate"
    rng = random.Random(SEED)
    shuffled = list(SHATTERED_INFO)
    rng.shuffle(shuffled)
    disambig = DisambigMockProvider()
    focus_baby_id = None
    history_parts = []

    for i, (msg, baby_key, attrs) in enumerate(shuffled, 1):
        history_text = "\n".join(history_parts[-5:]) if history_parts else ""
        gt_name = BABY_GROUND_TRUTHS[baby_key]["name"]

        # 在调用 resolve_and_archive 之前，检查 known 状态
        known = store.list_for_employee(ent, emp)
        customer = attrs.get("customer", "")
        baby = attrs.get("baby", "")
        if customer and baby:
            cid, bid = _match_known(known, customer, baby)
            # 找到 known 中该宝宝的信息
            target_info = None
            for it in known:
                if _norm(it.get("baby_name", "")) == _norm(baby):
                    target_info = it
                    break
            if cid is None and bid is None and target_info:
                print(f"  [TRACE] 序号{i}: 匹配失败！baby={baby}, customer={customer}")
                print(f"          known 中 {baby} 的 customer_name={target_info.get('customer_name')}")
                print(f"          _norm(customer)={_norm(customer)}, _norm(known_cname)={_norm(target_info.get('customer_name', ''))}")
                print(f"          是否相等: {_norm(customer) == _norm(target_info.get('customer_name', ''))}")

        result = await resolve_and_archive(
            store, disambig, ent, emp, history_text, msg, focus_baby_id
        )
        focus_baby_id = result.focus_baby_id
        history_parts.append(f"user: {msg}")

asyncio.run(main())
