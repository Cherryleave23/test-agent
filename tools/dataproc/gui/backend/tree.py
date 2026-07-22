"""树状列举：固定三类顶层 + 任意多层嵌套。"""
import os

from dataproc.repo import TOP_FOLDERS

from .repos import get_repo


def list_tree(name: str, path: str = "", base: str = None) -> dict:
    repo_dir, _meta = get_repo(name, base)
    target = os.path.join(repo_dir, path) if path else repo_dir
    target = os.path.normpath(target)
    if not (target == repo_dir or target.startswith(repo_dir + os.sep)):
        raise ValueError("非法路径（越界）")
    if not os.path.isdir(target):
        raise FileNotFoundError("文件夹不存在")

    folders, files = [], []
    for entry in sorted(os.listdir(target)):
        if entry.startswith("."):
            continue
        full = os.path.join(target, entry)
        rel = os.path.relpath(full, repo_dir).replace(os.sep, "/")
        if os.path.isdir(full):
            folders.append({"name": entry, "path": rel})
        else:
            files.append({"name": entry, "path": rel, "size": os.path.getsize(full)})
    return {"path": path, "folders": folders, "files": files, "top_folders": TOP_FOLDERS}
