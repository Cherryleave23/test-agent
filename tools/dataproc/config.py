"""dataproc 工具自带配置（零 src.* 依赖）。

配置层级（后者覆盖前者）：
  1. 内置 DEFAULTS
  2. 同目录/指定路径的 config.yaml
  3. 环境变量覆盖（便于 CI/端侧免改文件）：
       DATAPROC_OCR_ENABLED   -> 是否允许 OCR 路径（默认 false）
       RUN_REAL_OCR=1         -> 真正调用 PaddleOCR（否则扫描件/图片保持 ocr_pending 占位）
       DATAPROC_LLM_KIND      -> none | openai | ollama
       DATAPROC_LLM_BASE_URL  -> LLM 兼容端点
       DATAPROC_LLM_MODEL     -> 模型名
       DATAPROC_LLM_API_KEY   -> 凭据（仅工具自身，与 agent 不共享）
       DATAPROC_KNOWN_CATALOG -> 已知商品目录 ndjson 路径（实体解析用）

设计要点（PRD P2/P3）：
- OCR 为 Tier1 大依赖，端侧可选装；默认不触发，保证纯文本/结构化源在无 OCR 环境也能跑。
- LLM 为工具自带 provider，与 agent 完全解耦、凭据互不可见。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

try:
    import yaml
except ImportError:  # yaml 缺失时退化为纯 env/默认（不阻断工具运行）
    yaml = None  # type: ignore


DEFAULTS: dict = {
    "ocr_enabled": False,
    "run_real_ocr": False,
    "llm": {"kind": "none", "base_url": "", "model": "", "api_key": ""},
    "known_catalog": "",
}


@dataclass
class LLMConfig:
    kind: str = "none"          # none | openai | ollama
    base_url: str = ""
    model: str = ""
    api_key: str = ""

    @property
    def enabled(self) -> bool:
        return self.kind not in ("none", "")


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
                    "model": self.llm.model, "api_key": ("<set>" if self.llm.api_key else "")},
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
        ),
        known_catalog=merged.get("known_catalog", "") or "",
    )
