#!/usr/bin/env python3
# @module deploy
"""端侧环境变量覆盖验收（MOD-deploy §1B Windows 直装）。

验证 EnterpriseConfig.from_yaml_with_env() 的环境变量覆盖能力：
  - yaml 基线值被环境变量正确覆盖
  - 端侧不改 yaml 文件即可切换 LLM/embedding 模式
  - 数值类型（temperature/max_tokens）正确转换
  - 未设置的环境变量保持 yaml 原值

直接运行：python3 test_deploy_env_override.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common.config import EnterpriseConfig  # noqa: E402

YAML_TEMPLATE = textwrap.dedent("""\
    enterprise_id: baseline_ent
    enterprise_name: 基线门店
    db_path: ./data/baseline.db
    llm:
      kind: mock
      model: default
      temperature: 0.2
      max_tokens: 1024
    embedding:
      kind: mock
    wechat:
      bot_token: baseline_token
""")


def _write_yaml(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _p1_env_overrides_llm_kind():
    """AGENT_LLM_KIND 覆盖 yaml 中的 llm.kind"""
    path = _write_yaml(YAML_TEMPLATE)
    try:
        os.environ["AGENT_LLM_KIND"] = "cloud"
        os.environ["AGENT_LLM_API_KEY"] = "sk-test-123"
        os.environ["AGENT_LLM_BASE_URL"] = "https://api.deepseek.com/v1"
        os.environ["AGENT_LLM_MODEL"] = "deepseek-chat"
        cfg = EnterpriseConfig.from_yaml_with_env(path)
        assert cfg.llm.kind == "cloud", f"llm.kind 应被覆盖为 cloud，实际 {cfg.llm.kind}"
        assert cfg.llm.api_key == "sk-test-123", f"api_key 未覆盖：{cfg.llm.api_key}"
        assert cfg.llm.base_url == "https://api.deepseek.com/v1"
        assert cfg.llm.model == "deepseek-chat"
    finally:
        for k in ("AGENT_LLM_KIND", "AGENT_LLM_API_KEY", "AGENT_LLM_BASE_URL", "AGENT_LLM_MODEL"):
            os.environ.pop(k, None)
        os.unlink(path)


def _p2_env_overrides_embedding():
    """AGENT_EMBEDDING_KIND 覆盖 yaml 中的 embedding.kind"""
    path = _write_yaml(YAML_TEMPLATE)
    try:
        os.environ["AGENT_EMBEDDING_KIND"] = "bge-small-zh-v1.5"
        cfg = EnterpriseConfig.from_yaml_with_env(path)
        assert cfg.embedding.kind == "bge-small-zh-v1.5", \
            f"embedding.kind 应被覆盖，实际 {cfg.embedding.kind}"
    finally:
        os.environ.pop("AGENT_EMBEDDING_KIND", None)
        os.unlink(path)


def _p3_env_overrides_numeric():
    """数值类型正确转换：temperature(float) + max_tokens(int)"""
    path = _write_yaml(YAML_TEMPLATE)
    try:
        os.environ["AGENT_LLM_TEMPERATURE"] = "0.8"
        os.environ["AGENT_LLM_MAX_TOKENS"] = "4096"
        cfg = EnterpriseConfig.from_yaml_with_env(path)
        assert cfg.llm.temperature == 0.8, f"temperature 应为 0.8，实际 {cfg.llm.temperature}"
        assert isinstance(cfg.llm.temperature, float), "temperature 应为 float 类型"
        assert cfg.llm.max_tokens == 4096, f"max_tokens 应为 4096，实际 {cfg.llm.max_tokens}"
        assert isinstance(cfg.llm.max_tokens, int), "max_tokens 应为 int 类型"
    finally:
        os.environ.pop("AGENT_LLM_TEMPERATURE", None)
        os.environ.pop("AGENT_LLM_MAX_TOKENS", None)
        os.unlink(path)


def _p4_env_overrides_enterprise_and_db():
    """AGENT_ENTERPRISE_ID + AGENT_DB_PATH 覆盖"""
    path = _write_yaml(YAML_TEMPLATE)
    try:
        os.environ["AGENT_ENTERPRISE_ID"] = "store_001"
        os.environ["AGENT_DB_PATH"] = "D:/maternal-agent/data/store001.db"
        cfg = EnterpriseConfig.from_yaml_with_env(path)
        assert cfg.enterprise_id == "store_001"
        assert cfg.db_path == "D:/maternal-agent/data/store001.db"
    finally:
        os.environ.pop("AGENT_ENTERPRISE_ID", None)
        os.environ.pop("AGENT_DB_PATH", None)
        os.unlink(path)


def _p5_env_overrides_bot_token():
    """AGENT_BOT_TOKEN 覆盖 wechat.bot_token"""
    path = _write_yaml(YAML_TEMPLATE)
    try:
        os.environ["AGENT_BOT_TOKEN"] = "ilink_bot_abc123"
        cfg = EnterpriseConfig.from_yaml_with_env(path)
        assert cfg.wechat.bot_token == "ilink_bot_abc123"
    finally:
        os.environ.pop("AGENT_BOT_TOKEN", None)
        os.unlink(path)


def _p6_no_env_keeps_yaml_baseline():
    """未设置环境变量时，保持 yaml 原值（不破坏基线）"""
    # 确保没有 AGENT_ 开头的环境变量干扰
    agent_keys = [k for k in os.environ if k.startswith("AGENT_")]
    saved = {k: os.environ.pop(k) for k in agent_keys}
    try:
        path = _write_yaml(YAML_TEMPLATE)
        cfg = EnterpriseConfig.from_yaml_with_env(path)
        assert cfg.enterprise_id == "baseline_ent"
        assert cfg.llm.kind == "mock"
        assert cfg.embedding.kind == "mock"
        assert cfg.wechat.bot_token == "baseline_token"
        assert cfg.llm.temperature == 0.2
        os.unlink(path)
    finally:
        os.environ.update(saved)


def _p7_from_yaml_without_env_unchanged():
    """原 from_yaml 方法不受影响（向后兼容）"""
    path = _write_yaml(YAML_TEMPLATE)
    try:
        os.environ["AGENT_LLM_KIND"] = "cloud"
        # from_yaml（不带 _with_env）不应被环境变量影响
        cfg = EnterpriseConfig.from_yaml(path)
        assert cfg.llm.kind == "mock", "from_yaml 不应受环境变量影响"
    finally:
        os.environ.pop("AGENT_LLM_KIND", None)
        os.unlink(path)


CHECKS = [
    ("P1 AGENT_LLM_KIND 覆盖", _p1_env_overrides_llm_kind),
    ("P2 AGENT_EMBEDDING_KIND 覆盖", _p2_env_overrides_embedding),
    ("P3 数值类型转换", _p3_env_overrides_numeric),
    ("P4 企业ID+DB路径覆盖", _p4_env_overrides_enterprise_and_db),
    ("P5 BOT_TOKEN 覆盖", _p5_env_overrides_bot_token),
    ("P6 无环境变量保持基线", _p6_no_env_keeps_yaml_baseline),
    ("P7 from_yaml 向后兼容", _p7_from_yaml_without_env_unchanged),
]


def main():
    failed = []
    for name, fn in CHECKS:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {name}: 异常 {type(e).__name__}: {e}")
            failed.append(name)
    print(f"=== Summary: {len(CHECKS) - len(failed)}/{len(CHECKS)} passed ===")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("RESULT: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
