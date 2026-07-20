"""企业级运行时配置（每实例 = 1 家企业）。

配置驱动企业定制：LLM provider、iLink 凭证、embedding 模型、知识库路径。
端侧 1 企业 1 实例，故 enterprise_id 在启动时固定。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """每企业可配置的 LLM provider（C2）。

    kind: mock（测试）/ ollama（端侧）/ cloud（云 API）。
    """

    kind: str = Field(default="mock", description="mock | ollama | cloud")
    base_url: Optional[str] = None
    model: str = "default"
    api_key: Optional[str] = None  # 云 API 密钥；端侧可为空
    temperature: float = 0.2
    max_tokens: int = 1024


class EmbeddingConfig(BaseModel):
    kind: str = "mock"  # mock | bge-small-zh（默认本地）
    base_url: Optional[str] = None
    model: str = "mock"
    api_key: Optional[str] = None


class RerankConfig(BaseModel):
    """独立重排器（reranker）配置：与向量召回解耦的精度阶段。

    kind: none（透传，mock / 轻量场景，不加载模型）| bge-reranker-v2-m3（开源 cross-encoder）。
    """

    kind: str = "none"  # none | bge-reranker-v2-m3


class WechatConfig(BaseModel):
    """iLink Bot API 凭证与端点（C1/D1）。"""

    bot_token: str = ""
    app_id: str = "bot"
    base_url: str = "https://ilinkai.weixin.qq.com"
    poll_timeout: int = 30
    poll_interval: float = 1.0


class EnterpriseConfig(BaseModel):
    """单个端侧实例的企业配置（1 企业 1 实例，G6）。"""

    enterprise_id: str
    enterprise_name: str = ""
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    rerank: RerankConfig = Field(default_factory=RerankConfig)
    wechat: WechatConfig = Field(default_factory=WechatConfig)
    db_path: str = "instance.db"
    baby_profile_enabled: bool = True  # MOD-baby-profile：宝宝档案主动建档/归档/注入
    baby_db_path: Optional[str] = None  # 宝宝档案库路径；为空则复用 db_path
    system_prompt: str = (
        "你是母婴垂类智能顾问，服务于门店员工，基于企业产品知识库回答育儿与产品问题。"
    )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "EnterpriseConfig":
        import yaml  # type: ignore

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)
