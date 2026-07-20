"""每轮编排：消歧 + 建档安全网 + 主动归档 + 设焦点（MOD-baby-profile，P3 网关接线底层）。

把「消歧/抽取」(`resolution.resolve_and_extract`) 与「持久化」(`store`) 串起来，
对网关暴露一个干净协程；网关只负责拿结果注入 prompt 与落焦点。

安全网（混合式 + 待确认）：
- 第三人称/假设 → 不建档、不归档（绝不污染真实客户档案）
- 全新宝宝 → 自动建档但 `status=pending`（待员工确认/修正）
- 已建档宝宝 → 抽取到的明确属性 upsert 累积进档案（主动归档）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from baby.models import BabyProfile
from baby.store import BabyProfileStore
from baby.resolution import resolve_and_extract


@dataclass
class ArchiveResult:
    focus_baby_id: Optional[int]
    baby: Optional[BabyProfile]   # 供注入 prompt 的「当前焦点档案」（可能为 None）
    created: bool = False          # 本轮回合是否新建了宝宝档案
    action: str = "chat"
    parse_failed: bool = False     # 本轮 LLM 消歧解析失败（供会话级熔断统计）


async def resolve_and_archive(
    store: BabyProfileStore,
    provider,
    ent: str,
    emp: str,
    history_text: str,
    current_msg: str,
    focus_baby_id: Optional[int],
) -> ArchiveResult:
    known = store.list_for_employee(ent, emp)
    res = await resolve_and_extract(
        history_text, current_msg, known, focus_baby_id, provider
    )

    # 第三人称 / 假设 → 安全网：不建档、不归档，沿用焦点
    if res.is_third_party or res.is_hypothetical:
        return ArchiveResult(
            focus_baby_id=focus_baby_id,
            baby=store.get_baby(focus_baby_id) if focus_baby_id else None,
            action=res.action,
            parse_failed=res.parse_failed,
        )

    action = res.action

    # 已匹配到建档宝宝
    if res.baby_id is not None:
        bid = res.baby_id
        # 主动归档：把抽取的明确属性 upsert 进档案（跨轮累积）
        if res.extracted and not res.extracted.is_empty_attr():
            store.upsert_baby_attrs(bid, res.extracted)
        if action == "confirm":
            store.mark_confirmed(bid)
        elif action == "delete":
            store.delete_baby(bid)
            if focus_baby_id == bid:
                focus_baby_id = None
            return ArchiveResult(focus_baby_id=focus_baby_id, baby=None,
                                 action=action, parse_failed=res.parse_failed)
        # merge：合并需源/目标双名，当前消歧仅给目标；无可靠源则退化为 upsert 到目标，
        #        不做危险自动合并（可由后续显式修正指令增强）
        focus_baby_id = bid
        return ArchiveResult(
            focus_baby_id=focus_baby_id,
            baby=store.get_baby(bid),
            action=action,
            parse_failed=res.parse_failed,
        )

    # 未匹配已知宝宝 → 混合式自动建档（待确认安全网）
    if res.baby:
        cust_name = res.customer or "（未命名客户）"
        cid = store.get_or_create_customer(ent, emp, cust_name)
        existing = store.find_baby_by_name(ent, emp, res.baby)
        if existing is not None:
            bid = existing
            created = False
        else:
            baby = BabyProfile(
                baby_id=None, enterprise_id=ent, employee_id=emp,
                customer_id=cid, name=res.baby, status="pending",
            )
            bid = store.create_baby(baby)
            created = True
        if res.extracted and not res.extracted.is_empty_attr():
            store.upsert_baby_attrs(bid, res.extracted)
        if action == "confirm":
            store.mark_confirmed(bid)
        focus_baby_id = bid
        return ArchiveResult(
            focus_baby_id=focus_baby_id,
            baby=store.get_baby(bid),
            created=created,
            action=action,
            parse_failed=res.parse_failed,
        )

    # 兜底：沿用焦点（如纯产品知识问句，无任何宝宝指向）
    return ArchiveResult(
        focus_baby_id=focus_baby_id,
        baby=store.get_baby(focus_baby_id) if focus_baby_id else None,
        action=action,
        parse_failed=res.parse_failed,
    )
