"""处理编排：复用 dataproc 引擎 build_bundle，并写处理标记（防重复）。"""
import os

from dataproc.build import build_bundle, expand_selection

from .repos import get_repo
from .markers import is_processed, mark_processed


def process(name: str, selection: dict = None, base: str = None) -> dict:
    repo_dir, _meta = get_repo(name, base)
    out_dir = os.path.join(repo_dir, ".dataproc", "bundle")

    # 防重复：剔除已处理且内容哈希未变的文件
    all_files = expand_selection(repo_dir, selection)
    to_process = [f for f in all_files if not is_processed(name, f, base)]

    # 全部已处理且未变 → 直接跳过，不覆盖已有 bundle
    if not to_process:
        return {"out_dir": out_dir, "manifest": None,
                "processed_files": [], "skipped": len(all_files)}

    summary = build_bundle(repo_dir, out_dir, selection={"files": to_process})
    for rel in summary["processed_files"]:
        mark_processed(name, rel, "processed", "bundle", base)

    summary["skipped"] = len(all_files) - len(to_process)
    return summary
