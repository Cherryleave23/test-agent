#!/usr/bin/env python3
"""密钥防提交扫描（MOD-deploy P0-1）。

检查仓库是否可能提交了真实密钥/凭证，返回违例清单。
CI 用法：python3 scripts/secret_scan.py  → 退出码 0 无违例，非 0 有违例。

设计：
- 真实密钥模式（sk-/github_pat_/ghp_/xoxb-），排除明显占位符（<...>/xxx/example/获取）。
- .gitignore 必须忽略 .env*（否则密钥文件可能被提交）。
- .env.example 必须存在且含关键安全变量（AGENT_DATA_ENCRYPTION_KEY / AGENT_EGRESS_ENFORCE）。
- 扫描被 git 跟踪的文件；密钥文件（.env* / secrets/）不得被跟踪。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

# 真实密钥模式（排除明显占位符）
REAL_KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),           # OpenAI（排除 sk-xxx）
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),  # GitHub PAT
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),          # GitHub classic
    re.compile(r"xoxb-[A-Za-z0-9-]{20,}"),        # Slack
]

PLACEHOLDER_RE = re.compile(r"<[^>]*>|xxx+|your[-_]|example|占位|获取|placeholder", re.I)

# 密钥文件（不应被 git 跟踪）
SECRET_FILE_SUFFIXES = (".env", ".env.local", ".local")
SECRET_DIR_PREFIXES = ("secrets/",)


def _is_placeholder(text: str) -> bool:
    return bool(PLACEHOLDER_RE.search(text))


def _line_containing(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:end if end != -1 else len(text)]


def run_scan(repo_root: str) -> list[str]:
    """返回违例清单（空 = 通过）。"""
    violations: list[str] = []

    # 1) .gitignore 必须忽略 .env*
    gi = os.path.join(repo_root, ".gitignore")
    gi_text = open(gi, encoding="utf-8").read() if os.path.exists(gi) else ""
    if ".env" not in gi_text:
        violations.append(".gitignore 未忽略 .env*（密钥文件可能被提交）")

    # 2) .env.example 必须存在且含关键安全变量
    ex = os.path.join(repo_root, "deploy", ".env.example")
    if not os.path.exists(ex):
        violations.append("deploy/.env.example 缺失（端侧 env 模板）")
    else:
        et = open(ex, encoding="utf-8").read()
        for v in ("AGENT_DATA_ENCRYPTION_KEY", "AGENT_EGRESS_ENFORCE", "AGENT_BOT_TOKEN"):
            if v not in et:
                violations.append(f"deploy/.env.example 缺少变量 {v}")

    # 3) 扫描 git 跟踪的文件，找真实密钥 / 被跟踪的密钥文件
    try:
        tracked = subprocess.run(
            ["git", "-C", repo_root, "ls-files"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
    except Exception:
        tracked = []

    for rel in tracked:
        low = rel.lower()
        if low.startswith(SECRET_DIR_PREFIXES) or any(
            low.endswith(s) or (".env." in low and low.startswith(".env")) for s in SECRET_FILE_SUFFIXES
        ):
            violations.append(f"密钥文件被 git 跟踪：{rel}")
            continue
        if rel.endswith(".example") or rel.endswith(".md"):
            continue
        full = os.path.join(repo_root, rel)
        try:
            text = open(full, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for pat in REAL_KEY_PATTERNS:
            for m in pat.finditer(text):
                line = _line_containing(text, m.start())
                if line.strip().startswith("#") or _is_placeholder(line):
                    continue
                violations.append(f"{rel}: 疑似真实密钥 {m.group(0)[:12]}...")

    return violations


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    v = run_scan(root)
    if v:
        print("密钥扫描发现违例：")
        for x in v:
            print("  - " + x)
        return 1
    print("密钥扫描通过：未发现已提交的真实密钥")
    return 0


if __name__ == "__main__":
    sys.exit(main())
