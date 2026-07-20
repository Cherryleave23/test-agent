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

    @classmethod
    def from_yaml_with_env(cls, path: str | Path) -> "EnterpriseConfig":
        """加载 yaml 后用环境变量覆盖（端侧不改文件即可切换模式）。

        环境变量优先级高于 yaml。支持的环境变量（前缀 AGENT_）：
          AGENT_ENTERPRISE_ID     → enterprise_id
          AGENT_DB_PATH           → db_path
          AGENT_LLM_KIND          → llm.kind (mock|ollama|cloud)
          AGENT_LLM_MODEL         → llm.model
          AGENT_LLM_BASE_URL      → llm.base_url
          AGENT_LLM_API_KEY       → llm.api_key
          AGENT_LLM_TEMPERATURE   → llm.temperature (float)
          AGENT_LLM_MAX_TOKENS    → llm.max_tokens (int)
          AGENT_EMBEDDING_KIND    → embedding.kind (mock|bge-small-zh)
          AGENT_BOT_TOKEN         → wechat.bot_token

        用法（Windows）：
          set AGENT_LLM_KIND=cloud
          set AGENT_LLM_API_KEY=sk-xxx
          python src/main.py deploy/enterprise.yaml
        """
        import os

        cfg = cls.from_yaml(path)

        env = os.environ.get

        # 企业级
        if v := env("AGENT_ENTERPRISE_ID"):
            cfg.enterprise_id = v
        if v := env("AGENT_DB_PATH"):
            cfg.db_path = v

        # LLM
        if v := env("AGENT_LLM_KIND"):
            cfg.llm.kind = v
        if v := env("AGENT_LLM_MODEL"):
            cfg.llm.model = v
        if v := env("AGENT_LLM_BASE_URL"):
            cfg.llm.base_url = v
        if v := env("AGENT_LLM_API_KEY"):
            cfg.llm.api_key = v
        if v := env("AGENT_LLM_TEMPERATURE"):
            cfg.llm.temperature = float(v)
        if v := env("AGENT_LLM_MAX_TOKENS"):
            cfg.llm.max_tokens = int(v)

        # Embedding
        if v := env("AGENT_EMBEDDING_KIND"):
            cfg.embedding.kind = v

        # WeChat
        if v := env("AGENT_BOT_TOKEN"):
            cfg.wechat.bot_token = v

        return cfg
