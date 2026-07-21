"""LLM Provider 抽象与多实现（C2：每企业可配置）。

- MockProvider：确定性、无外部依赖，依据检索结果生成带引用的回答（harness 用）。
- OllamaProvider：端侧本地模型（/api/generate）。
- CloudProvider：云 API（OpenAI 兼容 chat/completions）。
- ProviderFactory：按配置实例化。

Prompt Caching：``complete`` 新增 ``cache_control`` 开关。开启时，provider 把
「稳定前缀」（通常是首条 system 消息）标记为可缓存断点，使同员工/同会话复用
前缀、降低 input token 费用。OpenAI 兼容端点靠自动前缀缓存（稳定前缀置首即生效）；
Anthropic 端点需显式 ``cache_control`` 断点（见 :func:`_apply_cache_control`）。
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Optional

from common.config import LLMConfig

logger = logging.getLogger(__name__)


# A6 修复：LLM 调用指数退避重试
LLM_MAX_RETRIES = 3
LLM_BASE_DELAY = 1.0


async def _complete_with_retry(coro_factory, max_retries: int = LLM_MAX_RETRIES,
                               base_delay: float = LLM_BASE_DELAY):
    """LLM 调用指数退避重试（A6）。

    对网络/超时/5xx/429 错误重试，对 4xx 客户端错误（除 429）不重试。
    重试间隔：base_delay * 2^attempt（1s, 2s, 4s）。
    """
    import httpx  # type: ignore

    last_exc = None
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except httpx.HTTPStatusError as e:
            last_exc = e
            # 4xx 客户端错误（除 429 Too Many Requests 外）不重试
            status = e.response.status_code
            if 400 <= status < 500 and status != 429:
                raise
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning("LLM 调用 HTTP %d，%ss 后重试 (%d/%d)",
                               status, delay, attempt + 1, max_retries - 1)
                await asyncio.sleep(delay)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_exc = e
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning("LLM 调用网络异常 %s，%ss 后重试 (%d/%d)",
                               type(e).__name__, delay, attempt + 1, max_retries - 1)
                await asyncio.sleep(delay)
    raise last_exc


def _apply_cache_control(messages: List[Dict], cfg: LLMConfig) -> List[Dict]:
    """按 provider 种类把「稳定前缀」标记为可缓存断点。

    - ``anthropic``：把首条 system 消息的内容包成 content-block 列表，并加
      ``cache_control: {type: "ephemeral"}`` 断点，使该前缀被缓存。
    - 其余（OpenAI 兼容 / ollama / mock）：自动前缀缓存已对「置首的稳定前缀」生效，
      无需改写请求体，原样返回即可（避免在标准 OpenAI 上发送不被接受的字段）。

    约定：调用方必须把「稳定、跨调用一致」的内容放在 ``messages[0]``（system），
    把每轮变量（当前句/历史/焦点）放在其后——这样无论哪种 provider，前缀都能命中缓存。
    """
    if getattr(cfg, "kind", None) != "anthropic":
        return messages
    out: List[Dict] = []
    for i, m in enumerate(messages):
        if i == 0 and m.get("role") == "system" and isinstance(m.get("content"), str):
            out.append({
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": m["content"],
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            })
        else:
            out.append(m)
    return out


def _report_cache_hit(usage: dict) -> Optional[float]:
    """解析 LLM 返回的 ``usage``，记录 Prompt Caching 命中情况（可观测性）。

    DeepSeek / OpenAI 在 ``usage.prompt_tokens_details.cached_tokens`` 返回命中 token 数；
    命中时记 info 日志并返回命中率（0-100），未命中或字段缺失返回 None。

    返回命中率便于单测断言，同时不依赖具体 logging 配置。
    """
    if not isinstance(usage, dict):
        return None
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens", 0) or 0
    total_in = usage.get("prompt_tokens", 0) or 0
    if cached > 0 and total_in > 0:
        hit_rate = cached / total_in * 100
        logger.info(
            "LLM 缓存命中: %d/%d tokens (%.0f%%)",
            cached, total_in, hit_rate,
        )
        return hit_rate
    return None


class LLMProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: List[Dict[str, str]],
        retrieved_hits: Optional[list] = None,
        cache_control: bool = False,
        **kw,
    ) -> str:
        ...


class MockProvider(LLMProvider):
    """测试用确定性 provider：直接基于检索命中生成回答，便于断言闭环正确性。"""

    async def complete(self, messages, retrieved_hits=None,
                       cache_control: bool = False, **kw) -> str:
        if not retrieved_hits:
            return "抱歉，当前知识库中暂无相关产品信息，建议您补充企业产品资料后再咨询。"
        # 零售问答：优先采用产品类命中（b_milk/b_nutrition），更贴合用户意图；
        # 无产品命中时回退到 HQ 通用知识（如育儿建议）。
        products = [h for h in retrieved_hits if h.part in ("b_milk", "b_nutrition")]
        top = products[0] if products else retrieved_hits[0]
        m = top.meta
        name = m.get("name") or top.title
        brand = m.get("brand", "")
        ptype = m.get("ptype") or m.get("category", "")
        stage = m.get("stage", "")
        age = m.get("age_range", "")
        price = m.get("price", "")
        hl = m.get("highlights", "")
        parts = [f"根据企业知识库，为您推荐：{name}"]
        if brand:
            parts.append(f"（{brand}")
            if ptype:
                parts[-1] += f"，{ptype}"
            if stage:
                parts[-1] += f"，{stage}段"
            parts[-1] += "）"
        if age:
            parts.append(f"适用{age}")
        if price != "":
            parts.append(f"官方参考价 {price} 元")
        if hl:
            parts.append(f"特点：{hl}")
        return "，".join(parts) + "。"


class OllamaProvider(LLMProvider):
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.base = (cfg.base_url or "http://localhost:11434").rstrip("/")

    async def complete(self, messages, retrieved_hits=None,
                       cache_control: bool = False, **kw) -> str:
        import httpx  # type: ignore

        # ollama /api/chat 默认 stream=true 返回 NDJSON，r.json() 会解析失败；
        # 必须显式 stream:false 才返回单条 JSON {"message": {"content": ...}}。
        # A6 修复：包裹在 _complete_with_retry 中，网络抖动时指数退避重试。
        async def _do_request():
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(
                    f"{self.base}/api/chat",
                    json={"model": self.cfg.model, "messages": messages,
                          "options": {"temperature": self.cfg.temperature},
                          "stream": False},
                )
                r.raise_for_status()
                data = r.json()
                return data.get("message", {}).get("content", "")

        return await _complete_with_retry(_do_request)


class CloudProvider(LLMProvider):
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")

    async def complete(self, messages, retrieved_hits=None,
                       cache_control: bool = False, **kw) -> str:
        import httpx  # type: ignore

        # Prompt Caching：仅对支持显式断点的 provider（Anthropic）改写请求体；
        # OpenAI 兼容端点靠自动前缀缓存（稳定前缀已置首），无需改动。
        if cache_control:
            messages = _apply_cache_control(messages, self.cfg)

        headers = {"Authorization": f"Bearer {self.cfg.api_key or ''}"}

        # A6 修复：包裹在 _complete_with_retry 中，网络抖动/5xx/429 时指数退避重试。
        async def _do_request():
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(
                    f"{self.base}/chat/completions",
                    headers=headers,
                    json={"model": self.cfg.model, "messages": messages,
                          "temperature": self.cfg.temperature,
                          "max_tokens": self.cfg.max_tokens},
                )
                r.raise_for_status()
                data = r.json()
                # Prompt Caching 可观测性：解析 usage 记录缓存命中（DeepSeek/OpenAI 均返回）
                _report_cache_hit(data.get("usage", {}) or {})
                return data["choices"][0]["message"]["content"]

        return await _complete_with_retry(_do_request)


class ProviderFactory:
    @staticmethod
    def get(cfg: LLMConfig) -> LLMProvider:
        if cfg.kind == "mock":
            return MockProvider()
        if cfg.kind == "ollama":
            return OllamaProvider(cfg)
        if cfg.kind == "cloud":
            return CloudProvider(cfg)
        raise ValueError(f"未知 LLM provider: {cfg.kind}")
