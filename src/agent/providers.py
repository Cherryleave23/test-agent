"""LLM Provider 抽象与多实现（C2：每企业可配置）。

- MockProvider：确定性、无外部依赖，依据检索结果生成带引用的回答（harness 用）。
- OllamaProvider：端侧本地模型（/api/generate）。
- CloudProvider：云 API（OpenAI 兼容 chat/completions）。
- ProviderFactory：按配置实例化。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Dict, Optional

from common.config import LLMConfig


class LLMProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: List[Dict[str, str]],
        retrieved_hits: Optional[list] = None,
        **kw,
    ) -> str:
        ...


class MockProvider(LLMProvider):
    """测试用确定性 provider：直接基于检索命中生成回答，便于断言闭环正确性。"""

    async def complete(self, messages, retrieved_hits=None, **kw) -> str:
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

    async def complete(self, messages, retrieved_hits=None, **kw) -> str:
        import httpx  # type: ignore

        # ollama /api/chat 默认 stream=true 返回 NDJSON，r.json() 会解析失败；
        # 必须显式 stream:false 才返回单条 JSON {"message": {"content": ...}}。
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


class CloudProvider(LLMProvider):
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")

    async def complete(self, messages, retrieved_hits=None, **kw) -> str:
        import httpx  # type: ignore

        headers = {"Authorization": f"Bearer {self.cfg.api_key or ''}"}
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{self.base}/chat/completions",
                headers=headers,
                json={"model": self.cfg.model, "messages": messages,
                      "temperature": self.cfg.temperature,
                      "max_tokens": self.cfg.max_tokens},
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]


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
