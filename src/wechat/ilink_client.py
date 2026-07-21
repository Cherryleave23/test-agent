"""iLink Bot API 客户端（MOD-wechat，C1/D1/D8）。

参考 Hermes weixin.py 的 iLink 契约：HTTP 长轮询 getUpdates + sync_buf 续传 +
context_token 回带 + ilink_bot_token 鉴权头 + message_id 去重 + 限流熔断。
仅用标准库（urllib + to_thread），无额外依赖；不耦合 Hermes 运行时。
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional

from common.config import WechatConfig
from common.egress import get_policy


@dataclass
class IncomingMessage:
    message_id: str
    from_user_id: str
    content: str
    msg_type: str = "text"


@dataclass
class PollResult:
    messages: list[IncomingMessage]
    sync_buf: Optional[str]
    context_token: Optional[str]


class RateLimitError(Exception):
    pass


class ILinkClient:
    def __init__(self, cfg: WechatConfig):
        self.cfg = cfg
        self._backoff_until = 0.0
        get_policy().allow(self.cfg.base_url)  # 端侧合法出网端点并入白名单

    def _headers(self) -> dict:
        return {
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self.cfg.bot_token}",
            "iLink-App-Id": self.cfg.app_id,
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.cfg.base_url.rstrip('/')}/{path.lstrip('/')}"
        get_policy().assert_allowed(url)  # P0-2 出网白名单（强制开启时拦截非白名单域名）
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.poll_timeout + 10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RateLimitError("iLink 限流")
            raise

    async def get_updates(self, sync_buf: Optional[str]) -> PollResult:
        # 限流退避
        now = time.time()
        if now < self._backoff_until:
            await asyncio.sleep(self._backoff_until - now)
        payload = {"sync_buf": sync_buf or "", "timeout": self.cfg.poll_timeout}
        try:
            raw = await asyncio.to_thread(self._post, "getupdates", payload)
        except RateLimitError:
            self._backoff_until = time.time() + 5
            return PollResult([], sync_buf, None)
        data = raw.get("data", {}) if isinstance(raw, dict) else {}
        msgs = []
        for m in data.get("messages", []) or []:
            msgs.append(
                IncomingMessage(
                    message_id=str(m.get("message_id", "")),
                    from_user_id=str(m.get("from_user_id", "")),
                    content=m.get("content", ""),
                    msg_type=m.get("msg_type", "text"),
                )
            )
        return PollResult(msgs, data.get("sync_buf"), data.get("context_token"))

    async def send_message(self, to_user_id: str, content: str,
                           context_token: Optional[str]) -> dict:
        payload = {
            "to_user_id": to_user_id,
            "content": content,
            "msg_type": "text",
            "context_token": context_token or "",
        }
        return await asyncio.to_thread(self._post, "sendmessage", payload)

    async def get_qr_code(self) -> str:
        raw = await asyncio.to_thread(self._post, "get_bot_qrcode", {})
        return (raw.get("data", {}) or {}).get("qr_code", "")

    async def get_qr_status(self) -> str:
        raw = await asyncio.to_thread(self._post, "get_qrcode_status", {})
        return (raw.get("data", {}) or {}).get("status", "")
