# @module scaffold
# 初始冒烟：验证 controlled-vibe-coding 治理骨架已就位（PRD 四件套 + 6 模块详解 + runner）。
# 这是第一个 harness，证明「真实运行判 PASS/FAIL」机制可用。
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PRD = os.path.join(ROOT, "prd")
MODULES_DIR = os.path.join(PRD, "modules")

REQUIRED = [
    os.path.join(PRD, "00-charter.md"),
    os.path.join(PRD, "01-architecture.md"),
    os.path.join(PRD, "02-index.md"),
    os.path.join(MODULES_DIR, "MOD-knowledge-ingest.md"),
    os.path.join(MODULES_DIR, "MOD-kb.md"),
    os.path.join(MODULES_DIR, "MOD-agent.md"),
    os.path.join(MODULES_DIR, "MOD-session.md"),
    os.path.join(MODULES_DIR, "MOD-wechat.md"),
    os.path.join(MODULES_DIR, "MOD-deploy.md"),
]
RUNNER = os.path.join(ROOT, "scripts", "run_harness.py")

missing = [p for p in REQUIRED if not os.path.isfile(p)]
if missing:
    for m in missing:
        print(f"  MISSING: {os.path.relpath(m, ROOT)}")
    print("RESULT: FAIL")
    sys.exit(1)

if not os.path.isfile(RUNNER):
    print("  MISSING: harness/run_harness.py")
    print("RESULT: FAIL")
    sys.exit(1)

# 索引表应登记全部 6 个模块
index_path = os.path.join(PRD, "02-index.md")
index_text = open(index_path, encoding="utf-8").read()
expected_modules = [
    "MOD-knowledge-ingest", "MOD-kb", "MOD-agent",
    "MOD-session", "MOD-wechat", "MOD-deploy",
]
missing_in_index = [m for m in expected_modules if m not in index_text]
if missing_in_index:
    print(f"  INDEX MISSING: {missing_in_index}")
    print("RESULT: FAIL")
    sys.exit(1)

print(f"RESULT: PASS  (scaffold ok: {len(REQUIRED)} files + runner + 6 indexed modules)")
