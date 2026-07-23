"""LLM 连通性测试客户端（dataproc GUI 自带，零 src.* 依赖）。

复用 agent providers.py 的「OpenAI 兼容 / Ollama」两类传输思路，但独立实现：
- OpenAI 兼容（lmstudio / cloud / openai）：GET {base_url}/models + 可选 chat/completions
- Ollama：GET {base_url}/api/tags

设计要点：
- transport 可注入（测试中用 stub 替代真实 httpx），使 harness 在无网环境也能跑真实逻辑。
- 凭据不记录到日志；返回结构化结果供前端展示。
"""
from __future__ import annotations

import json
import time
from typing import Callable, Optional

from dataproc.config import LLM_DEFAULT_BASE_URL, LLM_KINDS

# 真实传输：用 urllib（标准库，零额外依赖）。返回 (status_code, text)。
def _urllib_transport(method: str, url: str, headers: Optional[dict] = None,
                      body: Optional[dict] = None, timeout: float = 10):
    import urllib.request
    import urllib.error

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # URLError / timeout 等
        raise


def default_base_url(kind: str) -> str:
    return LLM_DEFAULT_BASE_URL.get(kind, "")


def _parse_models_openai(text: str) -> list:
    try:
        data = json.loads(text)
    except Exception:
        return []
    out = []
    for m in data.get("data", []) or []:
        mid = m.get("id") or m.get("name")
        if mid:
            out.append(str(mid))
    return out


def _parse_models_ollama(text: str) -> list:
    try:
        data = json.loads(text)
    except Exception:
        return []
    out = []
    for m in data.get("models", []) or []:
        name = m.get("name") or m.get("model")
        if name:
            out.append(str(name))
    return out


def test_connection(cfg: dict, transport: Optional[Callable] = None) -> dict:
    """真实连通测试（transport 可注入，便于单测）。

    入参 cfg: {kind, base_url, model, api_key}
    返回: { ok, latency_ms, models:list, endpoint:str, error:str|None, kind:str }
    """
    transport = transport or _urllib_transport
    kind = str(cfg.get("kind", "none")).lower()
    if kind == "openai":
        kind = "cloud"
    if kind not in LLM_KINDS or kind == "none":
        return {"ok": False, "latency_ms": 0, "models": [],
                "endpoint": "", "error": "LLM 未启用 (kind=none)", "kind": kind}

    base = (cfg.get("base_url") or default_base_url(kind) or "").strip().rstrip("/")
    api_key = cfg.get("api_key") or ""
    if not base:
        return {"ok": False, "latency_ms": 0, "models": [],
                "endpoint": "", "error": "base_url 为空（无法连接）", "kind": kind}

    t0 = time.time()
    try:
        if kind in ("lmstudio", "cloud"):
            # OpenAI 兼容：GET /models
            url = f"{base}/models"
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            status, text = transport("GET", url, headers=headers, timeout=10)
            if status >= 400:
                return {"ok": False, "latency_ms": int((time.time() - t0) * 1000),
                        "models": [], "endpoint": url,
                        "error": f"HTTP {status}: {text[:200]}", "kind": kind}
            models = _parse_models_openai(text)
            # 若 /models 不支持（部分本地端点），用 chat/completions 微型探测
            if not models and api_key:
                cstatus, ctext = transport(
                    "POST", f"{base}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}",
                              "Content-Type": "application/json"},
                    body={"model": cfg.get("model") or "",
                          "messages": [{"role": "user", "content": "ping"}],
                          "max_tokens": 4},
                    timeout=10,
                )
                if cstatus < 400:
                    models = [cfg.get("model") or "(未知)"]
                else:
                    return {"ok": False, "latency_ms": int((time.time() - t0) * 1000),
                            "models": [], "endpoint": f"{base}/chat/completions",
                            "error": f"HTTP {cstatus}: {ctext[:200]}", "kind": kind}
        elif kind == "ollama":
            url = f"{base}/api/tags"
            status, text = transport("GET", url, timeout=10)
            if status >= 400:
                return {"ok": False, "latency_ms": int((time.time() - t0) * 1000),
                        "models": [], "endpoint": url,
                        "error": f"HTTP {status}: {text[:200]}", "kind": kind}
            models = _parse_models_ollama(text)
        else:
            return {"ok": False, "latency_ms": 0, "models": [],
                    "endpoint": "", "error": f"未知 kind: {kind}", "kind": kind}
    except Exception as e:
        return {"ok": False, "latency_ms": int((time.time() - t0) * 1000),
                "models": [], "endpoint": base,
                "error": f"{type(e).__name__}: {e}", "kind": kind}

    return {"ok": True, "latency_ms": int((time.time() - t0) * 1000),
            "models": models, "endpoint": base, "error": None, "kind": kind}
