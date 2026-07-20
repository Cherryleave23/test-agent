"""微信网关（MOD-wechat，G5）：把 iLink 消息按 from_user_id 路由到
session → agent → 回复，保证多员工会话独立（D4）。

- 员工身份 = iLink 消息 from_user_id（C1）
- 会话主键 = (enterprise_id, employee_id, conversation_id)（D4）
- 每会话单锁串行化；message_id 去重防重放
- context_token 随 getUpdates 回带，sendmessage 续传对话连续性
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from common.config import EnterpriseConfig
from session.store import SessionStore
from session.constraints import (
    UserConstraints,
    extract_constraints,
    summarize_to_constraints,
    should_compress,
)
from agent.pipeline import Agent, Answer
from baby.store import BabyProfileStore
from baby.archive import resolve_and_archive
from wechat.ilink_client import ILinkClient, IncomingMessage

logger = logging.getLogger(__name__)

# 宝宝消歧连续解析失败阈值：达到即熔断降级为「仅产品问答」（缺陷 A 防静默归错档）
BABY_RESOLUTION_FAIL_THRESHOLD = 3


class WechatGateway:
    def __init__(self, cfg: EnterpriseConfig, session: SessionStore,
                 agent: Agent, client: ILinkClient,
                 baby_store: BabyProfileStore | None = None):
        self.cfg = cfg
        self.session = session
        self.agent = agent
        self.client = client
        self.baby_store = baby_store
        self._sync_buf: Optional[str] = None

    async def handle_message(self, msg: IncomingMessage,
                             context_token: Optional[str]) -> Optional[Answer]:
        ent = self.cfg.enterprise_id
        emp = msg.from_user_id
        conv = msg.from_user_id  # DM：每员工一个会话
        key = self.session.session_key(ent, emp, conv)
        lock = await self.session.lock_for(key)
        async with lock:
            sid = self.session.get_or_create(ent, emp, conv)
            if self.session.seen_message(sid, msg.message_id):
                return None  # 去重：已处理
            history = [
                {"role": t.role, "content": t.content}
                for t in self.session.history(sid)
            ]
            # ---- P1 用户约束：方向 B 逐轮抽取累积 + 方向 A 超阈 LLM 压缩 ----
            stored = self.session.get_constraints(sid) or UserConstraints()
            early_text = "\n".join(f"{h['role']}: {h['content']}" for h in history)
            if should_compress(len(history)):
                # 方向 A：把早期对话（含本轮）压成结构化约束，限制有效信息量（非轮数）
                compressed = await summarize_to_constraints(
                    early_text + f"\nuser: {msg.content}", self.agent.provider
                )
                stored = compressed
            else:
                # 方向 B：规则抽取本轮约束并累积进既有状态（确定性、无 LLM）
                stored = stored.merge(extract_constraints(msg.content))
            self.session.save_constraints(sid, stored)

            # ---- MOD-baby-profile：每轮消歧 + 建档安全网 + 主动归档 + 设焦点 ----
            baby_block = None
            if self.cfg.baby_profile_enabled and self.baby_store is not None:
                focus_before = self.session.get_focus_baby(sid)
                fails = self.session.get_resolution_fails(sid)
                if fails >= BABY_RESOLUTION_FAIL_THRESHOLD:
                    # 熔断：连续 N 轮消歧解析失败 → 降级为仅产品问答，不再建档/归档
                    logger.warning(
                        "宝宝消歧连续失败 %d 轮（>=阈值 %d），本会话降级为仅产品问答",
                        fails, BABY_RESOLUTION_FAIL_THRESHOLD,
                    )
                    if focus_before is not None:
                        b = self.baby_store.get_baby(focus_before)
                        if b is not None:
                            cust = self.baby_store.get_customer(b.customer_id)
                            baby_block = b.to_prompt_block(
                                customer_name=cust.name if cust else ""
                            )
                else:
                    arch = await resolve_and_archive(
                        self.baby_store, self.agent.provider, ent, emp,
                        early_text, msg.content, focus_before,
                    )
                    # 熔断计数：解析失败累加，成功重置（缺陷 A）
                    if arch.parse_failed:
                        self.session.inc_resolution_fails(sid)
                    else:
                        self.session.reset_resolution_fails(sid)
                    self.session.set_focus_baby(sid, arch.focus_baby_id)
                    if arch.baby is not None:
                        cust = self.baby_store.get_customer(arch.baby.customer_id)
                        baby_block = arch.baby.to_prompt_block(
                            customer_name=cust.name if cust else ""
                        )

            ans = await self.agent.answer(
                msg.content, history, constraints=stored, baby_block=baby_block
            )
            self.session.append_turn(sid, "user", msg.content, msg.message_id)
            self.session.append_turn(sid, "assistant", ans.text)
            await self.client.send_message(emp, ans.text, context_token)
            return ans

    async def run_once(self) -> int:
        res = await self.client.get_updates(self._sync_buf)
        self._sync_buf = res.sync_buf
        count = 0
        for m in res.messages:
            await self.handle_message(m, res.context_token)
            count += 1
        return count

    async def run_forever(self):
        while True:
            try:
                await self.run_once()
            except Exception as e:  # 容错：单轮异常不影响整体
                logger.warning("gateway poll error: %s", e)
            await asyncio.sleep(self.cfg.wechat.poll_interval)
