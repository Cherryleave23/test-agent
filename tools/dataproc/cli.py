"""dataproc 命令行入口（standalone，零 src.* 依赖）。

用法：
  python -m dataproc.cli build --repo-dir <仓库绝对路径> [--out <bundle目录>]
                                  [--files a.md b.pdf ...] [--folders "产品资料/奶粉" ...]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# 让 `python -m dataproc.cli` 能从 tools/ 直接导入 dataproc 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataproc.build import build_bundle  # noqa: E402
from dataproc.repo import load_meta  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dataproc", description="standalone 数据处理工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="把仓库资料归一为 NDJSON bundle")
    b.add_argument("--repo-dir", required=True, help="仓库绝对路径（含 .dataproc/repo.json）")
    b.add_argument("--out", default=None, help="bundle 输出目录（默认 <repo>/.dataproc/bundle）")
    b.add_argument("--files", nargs="*", default=None, help="仅处理指定相对路径文件")
    b.add_argument("--folders", nargs="*", default=None, help="仅处理指定相对路径文件夹")

    args = ap.parse_args(argv)
    if args.cmd == "build":
        load_meta(args.repo_dir)  # 校验
        out = args.out or os.path.join(args.repo_dir, ".dataproc", "bundle")
        sel = None
        if args.files or args.folders:
            sel = {}
            if args.files:
                sel["files"] = args.files
            if args.folders:
                sel["folders"] = args.folders
        summary = build_bundle(args.repo_dir, out, selection=sel)
        print(json.dumps(summary["manifest"], ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
