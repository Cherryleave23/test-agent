"""dataproc 工具自带 LLM provider 抽象（零 src.* 依赖，与 agent 完全解耦）。

PRD P3：结构化抽取通过工具自身的 provider 完成，凭据与 agent 互不可见、不共享。
- 同步接口（批量离线任务，单文档=1 次 complete，不需要 async）。
- 支持 OpenAI 兼容端点（openai / ollama 同协议 / 自建网关）。
- kind=none 时 structurer 走纯规则兜底（不调用 LLM、不编造）。
- 无网络/无凭据时显式抛错，由调用方决定兜底，绝不静默伪造输出。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import requests

from ..config import LLMConfig, LLM_DEFAULT_BASE_URL


class ToolLLMProvider(ABC):
    kind: str = "base"

    @abstractmethod
    def complete(self, prompt: str, system: str = "") -> str:
        """返回模型文本输出。失败显式抛错，不返回伪造内容。"""

    @property
    def label(self) -> str:
        return self.kind


class MockProvider(ToolLLMProvider):
    """确定性 mock（harness 默认绿跑用）。返回预设文本，便于验证 LLM 接线分支。"""
    kind = "mock"

    def __init__(self, canned: str = ""):
        self.canned = canned

    def complete(self, prompt: str, system: str = "") -> str:
        return self.canned


class OpenAICompatProvider(ToolLLMProvider):
    """OpenAI 兼容 /v1/chat/completions（openai 云、ollama、自建网关通用）。"""
    kind = "openai_compat"

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout: float = 120.0):
        self.base = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, prompt: str, system: str = "") -> str:
        if not self.model:
            raise RuntimeError("LLM provider 未配置 model")
        url = f"{self.base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            resp = requests.post(url, json={"model": self.model, "messages": messages,
                                            "temperature": 0}, headers=headers,
                                 timeout=self.timeout)
        except requests.RequestException as e:
            raise RuntimeError(f"LLM 调用失败（网络/端点不可用）：{e}") from e
        if resp.status_code != 200:
            raise RuntimeError(f"LLM 调用失败 HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return data["choices"][0]["message"]["content"]


class OllamaProvider(OpenAICompatProvider):
    """Ollama 本地端点（OpenAI 兼容 /v1）。默认 base_url=http://localhost:11434/v1。"""
    kind = "ollama"

    def __init__(self, base_url: str = "", model: str = "", api_key: str = "",
                 timeout: float = 180.0):
        super().__init__(base_url or "http://localhost:11434/v1", model, api_key, timeout)


def from_config(cfg: LLMConfig) -> Optional[ToolLLMProvider]:
    """按配置构建 provider；kind=none/空 → 返回 None（调用方走规则兜底）。"""
    kind = (cfg.kind or "none").lower()
    if kind in ("none", ""):
        return None
    if kind == "mock":
        return MockProvider()
    if kind == "ollama":
        return OllamaProvider(cfg.base_url, cfg.model, cfg.api_key)
    if kind in ("openai", "openai_compat", "cloud"):
        return OpenAICompatProvider(cfg.base_url, cfg.model, cfg.api_key)
    if kind == "lmstudio":
        # OpenAI 兼容本地端点，默认 LMStudio 端口；用户可在 GUI 覆盖 base_url
        base = cfg.base_url or LLM_DEFAULT_BASE_URL.get("lmstudio", "http://localhost:1234/v1")
        return OpenAICompatProvider(base, cfg.model, cfg.api_key)
    raise ValueError(f"未知 LLM kind: {cfg.kind}")
