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
from agent.pipeline import Agent, Answer
from wechat.ilink_client import ILinkClient, IncomingMessage

logger = logging.getLogger(__name__)


class WechatGateway:
    def __init__(self, cfg: EnterpriseConfig, session: SessionStore,
                 agent: Agent, client: ILinkClient):
        self.cfg = cfg
        self.session = session
        self.agent = agent
        self.client = client
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
            ans = await self.agent.answer(msg.content, history)
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
