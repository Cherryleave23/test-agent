#!/usr/bin/env python3
# @module ingest
"""LLM 配置页验收（设计文档 §P5）。

  直接运行：python3 test_llm_settings.py  → 退出码 0 全过，非 0 有失败。
  覆盖：
    L1  roundtrip   POST /settings/llm 写入 → GET 读回，字段一致；api_key 脱敏
    L2  kind 校验   none→enabled=False；非法 kind 被拒（422）
    L3  env 注入    settings.json 写 llm → _apply_gui_env() → DATAPROC_LLM_* 环境变量
    L4  连接成功    stub transport 模拟 200 + models → ok=True, models 非空, latency>0
    L5  ollama      stub 模拟 /api/tags → ok=True
    L6  连接失败    stub 抛网络错 → ok=False, error 非空, 不崩溃
    L7  none 测试   kind=none → ok=False, error 含 "未启用"
    L8  预设 baseurl kind=lmstudio→http://localhost:1234/v1；ollama→http://localhost:11434
"""
import os
import sys
import json
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tools", "dataproc"))

from fastapi.testclient import TestClient

from gui.backend import main as gui_main
from gui.backend import llm_client
from gui.backend import repos
from gui.backend.process import _apply_gui_env


# ---- stub transport（无网环境也能跑真实逻辑） ----
class StubTransport:
    def __init__(self, status=200, body=None, raise_exc=None):
        self.status = status
        self.body = body if body is not None else {}
        self.raise_exc = raise_exc
        self.last = None

    def __call__(self, method, url, headers=None, body=None, timeout=10):
        self.last = (method, url, headers, body)
        if self.raise_exc:
            raise self.raise_exc
        return self.status, json.dumps(self.body)


def _client_with_settings(tmp_path):
    """把 repos 默认基址重定向到临时目录，返回 TestClient（隔离真实 settings.json）。"""
    repos._DEFAULT_BASE = str(tmp_path)
    return TestClient(gui_main.app)


def main():
    fails = []
    tmp = tempfile.mkdtemp(prefix="llm_set_")

    # L1：roundtrip + 脱敏
    try:
        c = _client_with_settings(tmp)
        payload = {"kind": "lmstudio", "base_url": "http://localhost:1234/v1",
                   "model": "qwen2.5-7b", "api_key": "", "temperature": 0.3, "max_tokens": 2048}
        r = c.post("/settings/llm", json=payload)
        assert r.status_code == 200, f"POST 应 200，实际 {r.status_code}"
        body = r.json()
        assert body["kind"] == "lmstudio" and body["model"] == "qwen2.5-7b", "回写字段不符"
        assert body["api_key"] == "<set>" or body["api_key"] == "", f"api_key 应脱敏，实际 {body['api_key']!r}"
        g = c.get("/settings/llm").json()
        assert g["kind"] == "lmstudio" and g["model"] == "qwen2.5-7b", "GET 回读不符"
        assert g["api_key"] in ("<set>", ""), "GET api_key 应脱敏"
        print("[PASS] L1 (roundtrip + 脱敏)")
    except AssertionError as e:
        fails.append(f"L1: {e}")
    except Exception as e:
        fails.append(f"L1: 异常 {type(e).__name__}: {e}")

    # L2：kind 校验（none + 非法）
    try:
        c = _client_with_settings(tmp)
        r = c.post("/settings/llm", json={"kind": "none", "model": ""})
        assert r.status_code == 200
        g = c.get("/settings/llm").json()
        assert g["kind"] == "none"
        bad = c.post("/settings/llm", json={"kind": "gemini", "model": "x"})
        assert bad.status_code == 422, f"非法 kind 应 422，实际 {bad.status_code}"
        print("[PASS] L2 (kind 校验)")
    except AssertionError as e:
        fails.append(f"L2: {e}")
    except Exception as e:
        fails.append(f"L2: 异常 {type(e).__name__}: {e}")

    # L3：env 注入（注意 process.py 把 _DEFAULT_BASE 绑定为导入时副本，需直接 patch 模块级变量）
    try:
        os.environ.pop("DATAPROC_LLM_KIND", None)
        os.environ.pop("DATAPROC_LLM_BASE_URL", None)
        os.environ.pop("DATAPROC_LLM_MODEL", None)
        os.environ.pop("DATAPROC_LLM_API_KEY", None)
        os.environ.pop("DATAPROC_LLM_TEMPERATURE", None)
        os.environ.pop("DATAPROC_LLM_MAX_TOKENS", None)
        import gui.backend.process as proc_mod
        repos._DEFAULT_BASE = tmp  # process._apply_gui_env 动态读 repos._DEFAULT_BASE
        sp = os.path.join(tmp, repos.SETTINGS_FILE)
        with open(sp, "w", encoding="utf-8") as f:
            json.dump({"llm": {"kind": "cloud", "base_url": "https://api.x.com/v1",
                               "model": "deepseek-chat", "api_key": "sk-abc",
                               "temperature": 0.1, "max_tokens": 512}}, f)
        _apply_gui_env()
        assert os.environ.get("DATAPROC_LLM_KIND") == "cloud"
        assert os.environ.get("DATAPROC_LLM_BASE_URL") == "https://api.x.com/v1"
        assert os.environ.get("DATAPROC_LLM_MODEL") == "deepseek-chat"
        assert os.environ.get("DATAPROC_LLM_API_KEY") == "sk-abc"
        assert os.environ.get("DATAPROC_LLM_TEMPERATURE") == "0.1"
        assert os.environ.get("DATAPROC_LLM_MAX_TOKENS") == "512"
        # none 时清理
        with open(sp, "w", encoding="utf-8") as f:
            json.dump({"llm": {"kind": "none"}}, f)
        _apply_gui_env()
        assert "DATAPROC_LLM_KIND" not in os.environ, "none 应清除 env"
        print("[PASS] L3 (env 注入)")
    except AssertionError as e:
        fails.append(f"L3: {e}")
    except Exception as e:
        fails.append(f"L3: 异常 {type(e).__name__}: {e}")

    # L4：连接成功（OpenAI 兼容）
    try:
        stub = StubTransport(200, {"data": [{"id": "qwen2.5-7b"}, {"id": "deepseek-chat"}]})
        res = llm_client.test_connection(
            {"kind": "lmstudio", "base_url": "http://localhost:1234/v1", "model": "qwen2.5-7b", "api_key": ""},
            transport=stub)
        assert res["ok"] is True, f"应 ok=True，实际 {res}"
        assert len(res["models"]) == 2, f"应解析 2 个模型，实际 {res['models']}"
        assert res["latency_ms"] >= 0
        assert "/models" in stub.last[1]
        print("[PASS] L4 (连接成功 /models)")
    except AssertionError as e:
        fails.append(f"L4: {e}")
    except Exception as e:
        fails.append(f"L4: 异常 {type(e).__name__}: {e}")

    # L5：ollama
    try:
        stub = StubTransport(200, {"models": [{"name": "llama3.1"}, {"name": "qwen2.5"}]})
        res = llm_client.test_connection(
            {"kind": "ollama", "base_url": "http://localhost:11434", "model": "llama3.1", "api_key": ""},
            transport=stub)
        assert res["ok"] is True and len(res["models"]) == 2, f"ollama 应 ok，实际 {res}"
        assert "/api/tags" in stub.last[1]
        print("[PASS] L5 (ollama /api/tags)")
    except AssertionError as e:
        fails.append(f"L5: {e}")
    except Exception as e:
        fails.append(f"L5: 异常 {type(e).__name__}: {e}")

    # L6：连接失败（网络异常）
    try:
        import urllib.error
        stub = StubTransport(raise_exc=ConnectionError("refused"))
        res = llm_client.test_connection(
            {"kind": "cloud", "base_url": "https://api.x.com/v1", "model": "m", "api_key": "k"},
            transport=stub)
        assert res["ok"] is False, f"失败应 ok=False，实际 {res}"
        assert res["error"], "失败应有 error"
        print("[PASS] L6 (连接失败不崩溃)")
    except AssertionError as e:
        fails.append(f"L6: {e}")
    except Exception as e:
        fails.append(f"L6: 异常 {type(e).__name__}: {e}")

    # L7：none 测试
    try:
        res = llm_client.test_connection({"kind": "none", "base_url": "", "model": "", "api_key": ""})
        assert res["ok"] is False and "未启用" in (res["error"] or ""), f"none 应未启用，实际 {res}"
        print("[PASS] L7 (none 未启用)")
    except AssertionError as e:
        fails.append(f"L7: {e}")
    except Exception as e:
        fails.append(f"L7: 异常 {type(e).__name__}: {e}")

    # L8：预设 base_url
    try:
        assert llm_client.default_base_url("lmstudio") == "http://localhost:1234/v1"
        assert llm_client.default_base_url("ollama") == "http://localhost:11434"
        # 缺 base_url 时仍能用预设（stub 成功）
        stub = StubTransport(200, {"data": [{"id": "m"}]})
        res = llm_client.test_connection(
            {"kind": "lmstudio", "base_url": "", "model": "m", "api_key": ""}, transport=stub)
        assert res["ok"] is True and "1234/v1/models" in stub.last[1], f"预设 base_url 未生效 {stub.last[1]}"
        print("[PASS] L8 (预设 base_url)")
    except AssertionError as e:
        fails.append(f"L8: {e}")
    except Exception as e:
        fails.append(f"L8: 异常 {type(e).__name__}: {e}")

    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL GREEN (LLM 配置页：L1 roundtrip / L2 校验 / L3 env / L4-L8 连接测试)")
    sys.exit(0)


if __name__ == "__main__":
    main()
