"""出入站白名单（MOD-deploy P0-2）。

端侧 1 家 1 实例，运行期仅应出网到腾讯 iLink 官方域名（Bot API / 媒体 CDN）及
显式配置的 LLM/Embedding 端点。本模块提供**应用层出网域名强制**，作为 Docker
网络隔离（``wechat_egress``）之外的第二道防线。

设计要点：
- 默认域名白名单：``ilinkai.weixin.qq.com``（Bot API）+ ``novac2c.cdn.weixin.qq.com``（媒体 CDN）。
- LLM/Embedding 的 ``base_url`` 主机由对应客户端在构造时通过 :meth:`EgressPolicy.allow`
  并入白名单（否则合法云调用会被误拦）。
- 强制开关 ``AGENT_EGRESS_ENFORCE``：默认 ``0``（开发/mock 不拦截，避免破坏既有 harness）；
  **部署时置 ``1``** 才在 HTTP 客户端强制拦截非白名单域名。
- 逃生阀 ``AGENT_EGRESS_EXTRA_HOSTS``（逗号分隔）：部署侧临时放通额外域名。
- 不引入真实网络依赖即可单测（拦截发生在发请求之前）。
"""

from __future__ import annotations

import logging
import os
from typing import Iterable, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_HOSTS = {
    "ilinkai.weixin.qq.com",      # iLink Bot API
    "novac2c.cdn.weixin.qq.com",  # iLink 媒体 CDN
}


class EgressBlocked(RuntimeError):
    """出网目标不在白名单且强制开启时被抛出。"""


def _host_of(url_or_host: str) -> Optional[str]:
    """从 URL 或裸主机名提取小写 host（去端口）。无效返回 None。"""
    if not url_or_host:
        return None
    s = url_or_host.strip()
    if "://" in s:
        host = urlparse(s).hostname
    else:
        host = s.split("/", 1)[0].split(":", 1)[0]
    return host.lower() if host else None


class EgressPolicy:
    """出网域名白名单策略（单例，见 :func:`get_policy`）。"""

    def __init__(self, enforce: bool = False, extra_hosts: Iterable[str] = ()):
        self.enforce = enforce
        self._hosts = set(DEFAULT_ALLOWED_HOSTS)
        for h in extra_hosts:
            if h:
                self._hosts.add(h.lower())

    def allow(self, url_or_host: str) -> None:
        """并入白名单（用于 LLM/Embedding 等显式配置的合法端点）。"""
        h = _host_of(url_or_host)
        if h:
            self._hosts.add(h)

    def is_allowed(self, url_or_host: str) -> bool:
        h = _host_of(url_or_host)
        return h in self._hosts if h else False

    def assert_allowed(self, url_or_host: str) -> None:
        """强制开启且目标不在白名单则抛 :class:`EgressBlocked`；关闭时直接放行。"""
        if not self.enforce:
            return
        h = _host_of(url_or_host)
        if h and h in self._hosts:
            return
        raise EgressBlocked(
            f"出网被拦截：{url_or_host} 不在白名单（强制模式开启）。"
            f"当前白名单：{sorted(self._hosts)}"
        )


_POLICY: Optional[EgressPolicy] = None


def get_policy() -> EgressPolicy:
    """获取（缓存的）单例策略；首次调用从环境读取。"""
    global _POLICY
    if _POLICY is None:
        enforce = os.environ.get("AGENT_EGRESS_ENFORCE", "0") == "1"
        extra = [h.strip() for h in os.environ.get("AGENT_EGRESS_EXTRA_HOSTS", "").split(",") if h.strip()]
        _POLICY = EgressPolicy(enforce=enforce, extra_hosts=extra)
    return _POLICY


def reset_policy() -> None:
    """测试用：清空缓存。"""
    global _POLICY
    _POLICY = None


def configure_egress(*, enforce: Optional[bool] = None,
                      extra_hosts: Optional[Iterable[str]] = None,
                      llm_base_url: str = "", embedding_host: str = "") -> EgressPolicy:
    """重建策略（应用启动 / 测试注入）。``enforce=None`` 时沿用环境变量。"""
    global _POLICY
    env_enforce = os.environ.get("AGENT_EGRESS_ENFORCE", "0") == "1"
    eff_enforce = env_enforce if enforce is None else enforce
    hosts = [h.strip() for h in os.environ.get("AGENT_EGRESS_EXTRA_HOSTS", "").split(",") if h.strip()]
    if extra_hosts:
        hosts.extend([h for h in extra_hosts if h])
    _POLICY = EgressPolicy(enforce=eff_enforce, extra_hosts=hosts)
    if llm_base_url:
        _POLICY.allow(llm_base_url)
    if embedding_host:
        _POLICY.allow(embedding_host)
    return _POLICY


class AllowedAsyncClient:
    """``httpx.AsyncClient`` 包装：每次请求前经 :func:`get_policy` 校验白名单。

    强制关闭时行为等同原生 ``httpx.AsyncClient``（零开销透传）。
    """

    def __init__(self, *args, **kwargs):
        import httpx  # type: ignore

        self._client = httpx.AsyncClient(*args, **kwargs)

    async def __aenter__(self):
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._client.__aexit__(*exc)

    def _guard(self, url):
        get_policy().assert_allowed(url)

    async def post(self, url, **kwargs):
        self._guard(url)
        return await self._client.post(url, **kwargs)

    async def get(self, url, **kwargs):
        self._guard(url)
        return await self._client.get(url, **kwargs)

    async def request(self, method: str, url, **kwargs):
        self._guard(url)
        return await self._client.request(method, url, **kwargs)
