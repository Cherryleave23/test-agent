"""处理编排：复用 dataproc 引擎 build_bundle，并写处理标记（防重复）。

支持：
- force=True 时忽略已处理标记，强制重新处理
- out_dir 自定义输出目录（默认仓库 .dataproc/bundle）
"""
import os

from dataproc.build import build_bundle, expand_selection

from .repos import get_repo
from .markers import is_processed, mark_processed, clear_markers


def process(name: str, selection: dict = None, base: str = None,
            force: bool = False, out_dir: str = None) -> dict:
    repo_dir, _meta = get_repo(name, base)
    default_out = os.path.join(repo_dir, ".dataproc", "bundle")
    actual_out = out_dir or default_out

    # 展开所有文件
    all_files = expand_selection(repo_dir, selection)

    if force:
        # 强制模式：清除所有已处理标记，全部重新处理
        clear_markers(name, base)
        to_process = list(all_files)
    else:
        # 防重复：剔除已处理且内容哈希未变的文件
        to_process = [f for f in all_files if not is_processed(name, f, base)]

    # 全部已处理且未变 → 直接跳过，不覆盖已有 bundle
    if not to_process:
        return {"out_dir": actual_out, "manifest": None,
                "processed_files": [], "skipped": len(all_files)}

    summary = build_bundle(repo_dir, actual_out, selection={"files": to_process})
    for rel in summary["processed_files"]:
        mark_processed(name, rel, "processed", "bundle", base)

    summary["skipped"] = len(all_files) - len(to_process)
    summary["out_dir"] = actual_out
    return summary
