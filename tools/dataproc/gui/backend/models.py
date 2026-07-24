"""请求体模型。"""
from typing import Optional

from pydantic import BaseModel, field_validator

from dataproc.config import LLM_KINDS


class RepoCreate(BaseModel):
    name: str
    namespace: str = "b"  # b=企业自有；hq=总部共享库
    path: Optional[str] = None  # 自定义磁盘路径（None=使用默认 REPOS_BASE）
    output_dir: Optional[str] = None  # 每仓库独立输出目录（None=使用仓库内 .dataproc/bundle）


class ProcessRequest(BaseModel):
    selection: Optional[dict] = None  # None=全量；{"files":[...]} / {"folders":[...]}
    force: bool = False  # 强制重新处理（忽略已处理标记）
    out_dir: Optional[str] = None  # 自定义输出目录


class LLMSettings(BaseModel):
    """LLM provider 配置（与 agent common.config.LLMConfig 字段对齐：复用模式）。"""
    kind: str = "none"          # none | lmstudio | cloud | ollama（openai 视作 cloud 别名）
    base_url: str = ""
    model: str = ""
    api_key: str = ""           # 仅云端需要；本地可空
    temperature: float = 0.2
    max_tokens: int = 1024

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        if str(v).lower() not in LLM_KINDS:
            raise ValueError(f"kind 必须是 {LLM_KINDS} 之一，收到 {v!r}")
        return str(v).lower()


class SettingsUpdate(BaseModel):
    ocr_enabled: Optional[bool] = None
    run_real_ocr: Optional[bool] = None
    output_dir: Optional[str] = None
    repos_base: Optional[str] = None  # 仓库根目录
    llm: Optional[LLMSettings] = None  # 新增：LLM provider 子块


class SchemaField(BaseModel):
    key: str
    label: str = ""
    type: str = "text"
    required: bool = False
    aliases: Optional[list] = None


class SchemaDef(BaseModel):
    label: str
    kind: str = "flex"          # milk | nutrition | flex
    extends: Optional[str] = None
    keywords: Optional[list] = None
    fields: list = []           # List[SchemaField]


class SchemaUpdate(BaseModel):
    """POST /settings/schema 的请求体：企业自定义的 product_schemas 段。"""
    schemas: dict               # {schema_name: SchemaDef-ish}
