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


_UNNAMED_CUSTOMER = "（未命名客户）"
_AUTO_CONFIRM_THRESHOLD = 3  # D3: pending 宝宝累积 ≥3 个非空属性时自动转 confirmed


def _count_non_empty_attrs(baby: BabyProfile) -> int:
    """统计宝宝档案中非空属性数（D3: 信息丰度自动确认判据）。"""
    count = 0
    if baby.baby_age:
        count += 1
    if baby.stage:
        count += 1
    if baby.allergens:
        count += 1
    if baby.brand_preference:
        count += 1
    if baby.budget is not None:
        count += 1
    if baby.birth_date:
        count += 1
    if baby.gestational_weeks is not None:
        count += 1
    if baby.medical_history:
        count += 1
    if baby.feeding_history:
        count += 1
    return count


def _maybe_auto_confirm(store: BabyProfileStore, baby_id: int) -> None:
    """D3: 信息丰度自动确认——pending 宝宝累积 ≥3 个非空属性时自动转 confirmed。"""
    baby = store.get_baby(baby_id)
    if baby is None:
        return
    if baby.status == "pending" and _count_non_empty_attrs(baby) >= _AUTO_CONFIRM_THRESHOLD:
        store.mark_confirmed(baby_id)


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

    # D1 修复：取消 focus_is_stable 规则短路归档，每轮都走 LLM 路径。
    # 原逻辑：焦点稳定时跳过 LLM，用规则抽取归档（省一次 LLM 调用）。
    # 问题：规则抽取无法处理相对时间（"1年前3岁"→只抓到"3岁"）、开放词汇
    #       （"肚子疼"/"冰淇淋"不在词表里）、隐含推算（"1年前3岁"→可推算 birth_date）。
    # 新逻辑：每轮都走 LLM——LLM 既判归属又抽属性，能理解时序和开放词汇。
    # 规则抽取仅作为 LLM 解析失败时的兜底（见 _parse_resolution）。
    # 无宝宝信号的消息由 resolve_and_extract 入口预过滤拦截（C1 污染防护）。
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
        # D2 修复：如果 LLM 返回了客户名且当前客户名为「（未命名客户）」，更新客户名
        # 注意：多个宝宝可能共享同一「（未命名客户）」customer 记录，
        #       直接更新 name 会串档 → 创建新 customer 记录并更新 baby 的 customer_id 关联
        if res.customer and res.customer != _UNNAMED_CUSTOMER:
            cid_to_update = res.customer_id
            if cid_to_update is None:
                baby_row = store.get_baby(bid)
                if baby_row:
                    cid_to_update = baby_row.customer_id
            if cid_to_update:
                cust = store.get_customer(cid_to_update)
                if cust and cust.name == _UNNAMED_CUSTOMER:
                    # 创建新的独立 customer 记录，避免影响共享「（未命名客户）」的其他宝宝
                    new_cid = store.get_or_create_customer(ent, emp, res.customer)
                    store.update_baby_customer(bid, new_cid)
        # D3 修复：信息丰度自动确认
        _maybe_auto_confirm(store, bid)
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
        cust_name = res.customer or _UNNAMED_CUSTOMER
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
        # D2 修复：新建档时客户名为「（未命名客户）」，后续消息提供真实客户名时更新
        # 注意：共享 customer 记录串档问题同样存在于新建档路径
        if res.customer and res.customer != _UNNAMED_CUSTOMER:
            cust = store.get_customer(cid)
            if cust and cust.name == _UNNAMED_CUSTOMER:
                new_cid = store.get_or_create_customer(ent, emp, res.customer)
                store.update_baby_customer(bid, new_cid)
        # D3 修复：信息丰度自动确认
        _maybe_auto_confirm(store, bid)
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
