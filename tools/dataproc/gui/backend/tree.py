"""树状列举：固定三类顶层 + 任意多层嵌套。"""
import os

from dataproc.repo import TOP_FOLDERS

from .repos import get_repo


def list_tree(name: str, path: str = "", base: str = None) -> dict:
    """返回指定路径下的直接子项（文件夹+文件）。"""
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


def list_tree_full(name: str, base: str = None) -> dict:
    """递归返回仓库完整树结构（所有层级的文件和文件夹）。

    供前端 Obsidian 风格展开/折叠渲染使用。
    """
    repo_dir, _meta = get_repo(name, base)
    if not os.path.isdir(repo_dir):
        raise FileNotFoundError("仓库不存在")

    all_folders, all_files = [], []

    def walk(dir_path: str):
        try:
            entries = sorted(os.listdir(dir_path))
        except OSError:
            return
        for entry in entries:
            if entry.startswith("."):
                continue
            full = os.path.join(dir_path, entry)
            rel = os.path.relpath(full, repo_dir).replace(os.sep, "/")
            if os.path.isdir(full):
                all_folders.append({"name": entry, "path": rel})
                walk(full)
            else:
                all_files.append({"name": entry, "path": rel, "size": os.path.getsize(full)})

    walk(repo_dir)
    return {"path": "", "folders": all_folders, "files": all_files, "top_folders": TOP_FOLDERS}


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


def rmdir(name: str, folder_path: str, base: str = None) -> dict:
    """删除文件夹（仅允许空文件夹或用户确认的非空文件夹）。

    Args:
        name: 仓库名
        folder_path: 要删除的文件夹相对路径
    Returns:
        {"path": rel_path, "deleted": True}
    """
    import shutil
    repo_dir, _meta = get_repo(name, base)
    target = os.path.normpath(os.path.join(repo_dir, folder_path))
    if not (target.startswith(repo_dir + os.sep) or target == repo_dir):
        raise ValueError("非法路径（越界）")
    if target == repo_dir:
        raise ValueError("不能删除仓库根目录")
    if not os.path.isdir(target):
        raise FileNotFoundError("文件夹不存在")

    # 检查是否是三大总文件夹
    top_name = folder_path.split("/")[0] if "/" in folder_path else folder_path
    if top_name in TOP_FOLDERS and "/" not in folder_path:
        raise ValueError("不能删除三大总文件夹（产品资料/知识类文章/原料资料）")

    shutil.rmtree(target)
    rel = os.path.relpath(target, repo_dir).replace(os.sep, "/")
    return {"path": rel, "deleted": True}


def delete_file(name: str, file_path: str, base: str = None) -> dict:
    """删除单个文件。

    Args:
        name: 仓库名
        file_path: 要删除的文件相对路径
    Returns:
        {"path": rel_path, "deleted": True}
    """
    repo_dir, _meta = get_repo(name, base)
    target = os.path.normpath(os.path.join(repo_dir, file_path))
    if not target.startswith(repo_dir + os.sep):
        raise ValueError("非法路径（越界）")
    if not os.path.isfile(target):
        raise FileNotFoundError("文件不存在")
    os.remove(target)
    rel = os.path.relpath(target, repo_dir).replace(os.sep, "/")
    return {"path": rel, "deleted": True}


def move(name: str, src_path: str, dst_folder: str, base: str = None) -> dict:
    """移动文件或文件夹到目标文件夹。

    Args:
        name: 仓库名
        src_path: 源文件/文件夹相对路径
        dst_folder: 目标文件夹相对路径（空=仓库根）
    Returns:
        {"src": rel_src, "dst": rel_dst}
    """
    import shutil
    repo_dir, _meta = get_repo(name, base)
    src = os.path.normpath(os.path.join(repo_dir, src_path))
    dst_parent = os.path.join(repo_dir, dst_folder) if dst_folder else repo_dir
    dst_parent = os.path.normpath(dst_parent)

    if not (src.startswith(repo_dir + os.sep) or src == repo_dir):
        raise ValueError("非法源路径（越界）")
    if not (dst_parent == repo_dir or dst_parent.startswith(repo_dir + os.sep)):
        raise ValueError("非法目标路径（越界）")
    if not os.path.exists(src):
        raise FileNotFoundError("源文件/文件夹不存在")
    if not os.path.isdir(dst_parent):
        raise FileNotFoundError("目标文件夹不存在")

    basename = os.path.basename(src)
    dst = os.path.join(dst_parent, basename)
    if os.path.exists(dst):
        raise FileExistsError(f"目标位置已存在同名项: {basename}")

    shutil.move(src, dst)
    rel_src = os.path.relpath(src, repo_dir).replace(os.sep, "/")
    rel_dst = os.path.relpath(dst, repo_dir).replace(os.sep, "/")
    return {"src": rel_src, "dst": rel_dst}


def read_file(name: str, file_path: str, base: str = None) -> dict:
    """读取文件内容（仅限 .md/.txt）。

    Args:
        name: 仓库名
        file_path: 文件相对路径
    Returns:
        {"name": filename, "content": text, "size": bytes}
    """
    repo_dir, _meta = get_repo(name, base)
    target = os.path.normpath(os.path.join(repo_dir, file_path))
    if not target.startswith(repo_dir + os.sep):
        raise ValueError("非法路径（越界）")
    if not os.path.isfile(target):
        raise FileNotFoundError("文件不存在")
    ext = os.path.splitext(target)[1].lower()
    if ext not in (".md", ".txt"):
        raise ValueError("仅支持 .md/.txt 文件预览")
    with open(target, encoding="utf-8") as f:
        content = f.read()
    return {
        "name": os.path.basename(target),
        "content": content,
        "size": os.path.getsize(target),
    }
