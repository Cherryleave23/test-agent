"""dataproc 工具自带配置（零 src.* 依赖）。

配置层级（后者覆盖前者）：
  1. 内置 DEFAULTS
  2. 同目录/指定路径的 config.yaml
  3. 环境变量覆盖（便于 CI/端侧免改文件）：
       DATAPROC_OCR_ENABLED   -> 是否允许 OCR 路径（默认 false）
       RUN_REAL_OCR=1         -> 真正调用 PaddleOCR（否则扫描件/图片保持 ocr_pending 占位）
       DATAPROC_LLM_KIND      -> none | lmstudio | cloud | ollama（openai 视作 cloud 别名）
       DATAPROC_LLM_BASE_URL  -> LLM 兼容端点
       DATAPROC_LLM_MODEL     -> 模型名
       DATAPROC_LLM_API_KEY   -> 凭据（仅工具自身，与 agent 不共享）
       DATAPROC_LLM_TEMPERATURE -> 采样温度（float，默认 0.2）
       DATAPROC_LLM_MAX_TOKENS  -> 输出上限（int，默认 1024）
       DATAPROC_KNOWN_CATALOG -> 已知商品目录 ndjson 路径（实体解析用）

设计要点（PRD P2/P3）：
- OCR 为 Tier1 大依赖，端侧可选装；默认不触发，保证纯文本/结构化源在无 OCR 环境也能跑。
- LLM 为工具自带 provider，与 agent 完全解耦、凭据互不可见。
- LLM kind 域（none/lmstudio/cloud/ollama）由 WebUI「LLM 配置」页配置，字段结构与
  agent 的 common.config.LLMConfig 对齐（kind/base_url/model/api_key/temperature/max_tokens），
  仅做模式复用、不 import src.*（端侧独立部署约束）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

try:
    import yaml
except ImportError:  # yaml 缺失时退化为纯 env/默认（不阻断工具运行）
    yaml = None  # type: ignore


# LLM provider 类型白名单。lmstudio/cloud 均为 OpenAI 兼容（仅默认 base_url/是否需 key 不同）；
# openai 作为 cloud 的历史别名保留兼容。
LLM_KINDS = ("none", "lmstudio", "cloud", "ollama", "openai")

# 各 kind 的默认 base_url 预设（LMStudio/Ollama 端侧常用端口）
LLM_DEFAULT_BASE_URL = {
    "lmstudio": "http://localhost:1234/v1",
    "ollama": "http://localhost:11434",
    "cloud": "",
    "openai": "",
}


DEFAULTS: dict = {
    "ocr_enabled": False,
    "run_real_ocr": False,
    "llm": {"kind": "none", "base_url": "", "model": "", "api_key": "",
            "temperature": 0.2, "max_tokens": 1024},
    "known_catalog": "",
}


@dataclass
class LLMConfig:
    kind: str = "none"          # none | lmstudio | cloud | ollama（openai=cloud 别名）
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    temperature: float = 0.2    # 采样温度（Nice-to-have，沿用 agent LLMConfig）
    max_tokens: int = 1024      # 输出 token 上限（Nice-to-have）

    @property
    def enabled(self) -> bool:
        return self.kind not in ("none", "")

    @property
    def normalized_kind(self) -> str:
        """归一化：openai 别名统一为 cloud（传输层均为 OpenAI 兼容）。"""
        return "cloud" if self.kind == "openai" else self.kind


@dataclass
class DataprocConfig:
    ocr_enabled: bool = False
    run_real_ocr: bool = False
    llm: LLMConfig = field(default_factory=LLMConfig)
    known_catalog: str = ""

    def as_dict(self) -> dict:
        return {
            "ocr_enabled": self.ocr_enabled,
            "run_real_ocr": self.run_real_ocr,
            "llm": {"kind": self.llm.kind, "base_url": self.llm.base_url,
                    "model": self.llm.model,
                    "api_key": ("<set>" if self.llm.api_key else ""),
                    "temperature": self.llm.temperature,
                    "max_tokens": self.llm.max_tokens},
            "known_catalog": self.known_catalog,
        }


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Optional[str] = None) -> DataprocConfig:
    """加载并合并配置：DEFAULTS < config.yaml < 环境变量。"""
    cfg_path = path or os.environ.get("DATAPROC_CONFIG")
    if not cfg_path:
        # 默认同包目录 config.yaml
        here = os.path.dirname(os.path.abspath(__file__))
        cand = os.path.join(here, "config.yaml")
        cfg_path = cand if os.path.isfile(cand) else None

    merged = dict(DEFAULTS)
    if cfg_path and os.path.isfile(cfg_path) and yaml is not None:
        try:
            with open(cfg_path, encoding="utf-8") as f:
                file_cfg = yaml.safe_load(f) or {}
            merged = _deep_merge(merged, file_cfg)
        except Exception:
            pass  # 配置损坏不阻断，回落默认

    # 环境变量覆盖
    if os.environ.get("DATAPROC_OCR_ENABLED", "").lower() in ("1", "true", "yes"):
        merged["ocr_enabled"] = True
    if os.environ.get("RUN_REAL_OCR", "").lower() in ("1", "true", "yes"):
        merged["run_real_ocr"] = True
    llm = dict(merged.get("llm", {}))
    if os.environ.get("DATAPROC_LLM_KIND"):
        llm["kind"] = os.environ["DATAPROC_LLM_KIND"]
    if os.environ.get("DATAPROC_LLM_BASE_URL"):
        llm["base_url"] = os.environ["DATAPROC_LLM_BASE_URL"]
    if os.environ.get("DATAPROC_LLM_MODEL"):
        llm["model"] = os.environ["DATAPROC_LLM_MODEL"]
    if os.environ.get("DATAPROC_LLM_API_KEY"):
        llm["api_key"] = os.environ["DATAPROC_LLM_API_KEY"]
    if os.environ.get("DATAPROC_LLM_TEMPERATURE"):
        try:
            llm["temperature"] = float(os.environ["DATAPROC_LLM_TEMPERATURE"])
        except ValueError:
            pass
    if os.environ.get("DATAPROC_LLM_MAX_TOKENS"):
        try:
            llm["max_tokens"] = int(os.environ["DATAPROC_LLM_MAX_TOKENS"])
        except ValueError:
            pass
    # 归一化 kind：openai 别名 -> cloud；非法 kind -> none（安全回落）
    kind = str(llm.get("kind", "none")).lower()
    if kind == "openai":
        kind = "cloud"
    if kind not in LLM_KINDS:
        kind = "none"
    llm["kind"] = kind
    merged["llm"] = llm
    if os.environ.get("DATAPROC_KNOWN_CATALOG"):
        merged["known_catalog"] = os.environ["DATAPROC_KNOWN_CATALOG"]

    return DataprocConfig(
        ocr_enabled=bool(merged.get("ocr_enabled", False)),
        run_real_ocr=bool(merged.get("run_real_ocr", False)),
        llm=LLMConfig(
            kind=llm.get("kind", "none"),
            base_url=llm.get("base_url", ""),
            model=llm.get("model", ""),
            api_key=llm.get("api_key", ""),
            temperature=float(llm.get("temperature", 0.2)),
            max_tokens=int(llm.get("max_tokens", 1024)),
        ),
        known_catalog=merged.get("known_catalog", "") or "",
    )
