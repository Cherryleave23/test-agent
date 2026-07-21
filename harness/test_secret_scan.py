#!/usr/bin/env python3
# @module deploy
"""密钥防提交扫描（MOD-deploy P0-1）真实运行验收 harness。

按 CVC：真实扫描当前仓库 + 正向验证（临时仓库放真实密钥必被检出），断言 PASS/FAIL。
覆盖：
  S1 当前仓库扫描无违例（无已提交真实密钥、.env 被忽略、.env.example 合规）
  S2 .env.example 含关键安全变量（AGENT_DATA_ENCRYPTION_KEY / AGENT_EGRESS_ENFORCE / AGENT_BOT_TOKEN）
  S3 正向验证：临时 git 仓库放入真实密钥，扫描必检出（证明非真空过）
  S4 .gitignore 忽略 .env*（密钥文件不会入库）

直接运行：python3 test_secret_scan.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
import tempfile
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from secret_scan import run_scan  # noqa: E402


def s1_current_repo_clean():
    v = run_scan(ROOT)
    assert v == [], f"当前仓库应无密钥违例，实际：{v}"


def s2_env_example_has_vars():
    ex = os.path.join(ROOT, "deploy", ".env.example")
    assert os.path.exists(ex), "deploy/.env.example 应存在"
    t = open(ex, encoding="utf-8").read()
    for var in ("AGENT_DATA_ENCRYPTION_KEY", "AGENT_EGRESS_ENFORCE", "AGENT_BOT_TOKEN"):
        assert var in t, f".env.example 应含 {var}"


def s3_detects_real_key():
    # 临时 git 仓库，放入真实密钥，扫描必须检出
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    # 满足基础合规，避免无关违例
    open(os.path.join(d, ".gitignore"), "w", encoding="utf-8").write(".env\n")
    os.makedirs(os.path.join(d, "deploy"))
    open(os.path.join(d, "deploy", ".env.example"), "w", encoding="utf-8").write(
        "AGENT_DATA_ENCRYPTION_KEY=\nAGENT_EGRESS_ENFORCE=0\nAGENT_BOT_TOKEN=\n"
    )
    # 真实密钥（非占位符）
    open(os.path.join(d, "leak.txt"), "w", encoding="utf-8").write(
        "token = sk-realkey1234567890abcdef\n"
    )
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    v = run_scan(d)
    assert any("sk-realkey" in x or "疑似真实密钥" in x for x in v), \
        f"应检出真实密钥，实际违例：{v}"


def s4_gitignore_has_env():
    gi = os.path.join(ROOT, ".gitignore")
    assert os.path.exists(gi), ".gitignore 应存在"
    t = open(gi, encoding="utf-8").read()
    assert ".env" in t, ".gitignore 应忽略 .env*（密钥文件）"


CHECKS = [
    ("S1 当前仓库扫描无违例", s1_current_repo_clean),
    ("S2 .env.example 含关键安全变量", s2_env_example_has_vars),
    ("S3 正向验证：真实密钥必被检出", s3_detects_real_key),
    ("S4 .gitignore 忽略 .env*", s4_gitignore_has_env),
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
