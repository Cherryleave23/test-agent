"""拖拽上传：把文件落到当前打开的文件夹（不破坏现有结构）。

支持单文件和多文件上传（拖入文件夹时前端递归展开后逐个上传）。
"""
import os

from .repos import get_repo


def upload_file(name: str, folder: str, filename: str, data: bytes, base: str = None) -> dict:
    """上传单个文件到指定文件夹。

    Args:
        name: 仓库名
        folder: 目标文件夹相对路径（空=仓库根）
        filename: 文件名
        data: 文件二进制内容
    Returns:
        {"path": 相对路径, "size": 字节数}
    """
    repo_dir, _meta = get_repo(name, base)
    dest_dir = os.path.join(repo_dir, folder) if folder else repo_dir
    # 防路径穿越：folder 必须在仓库内
    if folder:
        norm = os.path.normpath(os.path.join(repo_dir, folder))
        if not (norm == repo_dir or norm.startswith(repo_dir + os.sep)):
            raise ValueError("非法文件夹路径")
    os.makedirs(dest_dir, exist_ok=True)
    safe_name = os.path.basename(filename)
    if not safe_name:
        raise ValueError("文件名为空")
    dest = os.path.join(dest_dir, safe_name)
    if not (os.path.normpath(dest).startswith(os.path.normpath(repo_dir))):
        raise ValueError("非法文件路径")
    with open(dest, "wb") as f:
        f.write(data)
    rel = os.path.relpath(dest, repo_dir).replace(os.sep, "/")
    return {"path": rel, "size": len(data)}
