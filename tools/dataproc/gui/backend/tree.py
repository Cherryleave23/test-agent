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


def mkdir(name: str, parent_path: str, folder_name: str, base: str = None) -> dict:
    """在 parent_path 下创建子文件夹 folder_name。

    Args:
        name: 仓库名
        parent_path: 父文件夹相对路径（空=仓库根）
        folder_name: 新文件夹名
    Returns:
        {"path": 相对路径}
    """
    repo_dir, _meta = get_repo(name, base)
    parent = os.path.join(repo_dir, parent_path) if parent_path else repo_dir
    parent = os.path.normpath(parent)
    if not (parent == repo_dir or parent.startswith(repo_dir + os.sep)):
        raise ValueError("非法父路径（越界）")

    # 防止文件夹名含非法字符
    safe_name = os.path.basename(folder_name.strip())
    if not safe_name or any(c in safe_name for c in '<>:"/\\|?*'):
        raise ValueError(f"非法文件夹名: {folder_name}")

    dest = os.path.join(parent, safe_name)
    os.makedirs(dest, exist_ok=True)
    rel = os.path.relpath(dest, repo_dir).replace(os.sep, "/")
    return {"path": rel}
