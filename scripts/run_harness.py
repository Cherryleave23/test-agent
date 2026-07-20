#!/usr/bin/env python3
"""Deterministic harness runner for the controlled-vibe-coding skill.

Discovers executable acceptance scripts in a harness directory and runs them,
reporting PASS/FAIL deterministically. A failing suite exits non-zero so it can
gate CI or an agent loop.

Two discovery modes:
  1. manifest.json present  -> use declared command/type/expect per test.
  2. no manifest            -> discover script files, run directly; module tag
                               read from a `# @module X` comment in the file.

Usage:
  python run_harness.py [--dir DIR] [--module M] [--tag T] [--all] [--timeout S]
"""
import argparse
import json
import os
import subprocess
import sys

INTERPRETERS = {
    ".py": [sys.executable or "python3"],
    ".js": ["node"],
    ".mjs": ["node"],
    ".ts": ["npx", "ts-node"],
    ".sh": ["bash"],
    ".bash": ["bash"],
    ".rb": ["ruby"],
}

# Binaries that may already appear as the first token of a command; do not
# prepend an interpreter when one of these leads the command.
KNOWN_BINARIES = {
    "python", "python3", "python.exe",
    "node", "node.exe",
    "bash", "sh", "rb", "ruby", "ruby.exe",
    "npx", "ts-node", "tsx",
    "pytest", "curl", "go", "java", "dotnet", "php",
}


def resolve_argv(command):
    """Turn a manifest command string into an argv, auto-prepending an
    interpreter when the first token is a bare script file."""
    tokens = command.split()
    if not tokens:
        return tokens
    first = tokens[0]
    base = os.path.basename(first).lower()
    ext = os.path.splitext(first)[1].lower()
    if ext in INTERPRETERS and base not in KNOWN_BINARIES:
        return INTERPRETERS[ext] + tokens
    return tokens


def discover_manifest(harness_dir):
    manifest = os.path.join(harness_dir, "manifest.json")
    if os.path.isfile(manifest):
        with open(manifest, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("tests", [])
    return None


def read_module_tag(filepath):
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for _ in range(20):
                line = f.readline()
                if not line:
                    break
                if line.strip().startswith("#") and "@module" in line:
                    return line.split("@module", 1)[1].strip()
    except OSError:
        pass
    return "untagged"


def discover_files(harness_dir):
    tests = []
    for name in sorted(os.listdir(harness_dir)):
        path = os.path.join(harness_dir, name)
        if not os.path.isfile(path) or name == "manifest.json":
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in INTERPRETERS:
            continue
        interp = INTERPRETERS[ext]
        command = interp + [path]
        tests.append({
            "id": os.path.splitext(name)[0],
            "module": read_module_tag(path),
            "description": name,
            "command": " ".join(command),
            "type": "exit-code",
            "_argv": command,
        })
    return tests


def run_one(test, timeout, cwd):
    argv = test.get("_argv")
    if argv is None:
        argv = resolve_argv(test["command"])
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"[TIMEOUT after {timeout}s]", 124
    except Exception as e:  # noqa: BLE001
        return False, f"[ERROR] {e}", 1

    out = (proc.stdout or "") + (proc.stderr or "")
    if test.get("type") == "output-contains":
        passed = test.get("expect", "RESULT: PASS") in out
    else:
        passed = proc.returncode == 0
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    return passed, tail, proc.returncode


def main():
    ap = argparse.ArgumentParser(description="Run acceptance harness scripts.")
    ap.add_argument("--dir", default="harness", help="Harness directory (default: harness)")
    ap.add_argument("--module", action="append", default=[], help="Filter by module (repeatable)")
    ap.add_argument("--tag", action="append", default=[], help="Alias for --module (repeatable)")
    ap.add_argument("--all", action="store_true", help="Run all tests (default when no filter)")
    ap.add_argument("--timeout", type=int, default=60, help="Per-test timeout seconds")
    args = ap.parse_args()

    harness_dir = os.path.abspath(args.dir)
    if not os.path.isdir(harness_dir):
        print(f"[SKIP] harness dir not found: {harness_dir}", file=sys.stderr)
        sys.exit(0)

    manifest_tests = discover_manifest(harness_dir)
    if manifest_tests is not None:
        tests = [dict(t) for t in manifest_tests]
    else:
        tests = discover_files(harness_dir)

    filters = set(args.module) | set(args.tag)
    if filters:
        tests = [t for t in tests if t.get("module") in filters]

    if not tests:
        print("[EMPTY] no harness tests matched.", file=sys.stderr)
        sys.exit(0)

    print(f"=== Harness run: {len(tests)} test(s) ===")
    passed_total = 0
    failures = []
    for t in tests:
        ok, tail, rc = run_one(t, args.timeout, harness_dir)
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] ({t.get('module')}) {t.get('description')}  rc={rc}")
        if tail:
            print(f"        -> {tail}")
        if ok:
            passed_total += 1
        else:
            failures.append((t, tail))

    print(f"=== Summary: {passed_total}/{len(tests)} passed ===")
    if failures:
        print("Failed tests:")
        for t, tail in failures:
            print(f"  - ({t.get('module')}) {t.get('description')}: {tail}")
        sys.exit(1)
    print("RESULT: ALL GREEN")
    sys.exit(0)


if __name__ == "__main__":
    main()
