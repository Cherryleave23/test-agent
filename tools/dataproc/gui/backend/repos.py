"""仓库管理：新建 / 列举 / 读取 / 切换。仓库 = 磁盘目录，映射 enterprise_id + namespace。

支持自定义磁盘路径（用户可在创建仓库时指定磁盘位置）。
仓库注册表：<REPOS_BASE>/repos.json 记录所有仓库的 name→disk_path 映射。
"""
import json
import os
import uuid
from typing import List, Tuple, Optional

from dataproc.repo import TOP_FOLDERS  # 复用引擎侧固定三类定义

from .config import REPOS_BASE
from .util import now_iso

META_FILE = ".dataproc/repo.json"
CURRENT_FILE = ".current"
REGISTRY_FILE = "repos.json"


def _registry_path(base: str) -> str:
    return os.path.join(base, REGISTRY_FILE)


def _load_registry(base: str = None) -> dict:
    """加载仓库注册表 name→{disk_path, ...meta}。"""
    base = base or REPOS_BASE
    p = _registry_path(base)
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_registry(reg: dict, base: str = None) -> None:
    base = base or REPOS_BASE
    os.makedirs(base, exist_ok=True)
    with open(_registry_path(base), "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)


def _repo_disk_path(base: str, name: str, custom_path: str = None) -> str:
    """返回仓库在磁盘上的实际路径。"""
    if custom_path:
        return os.path.abspath(custom_path)
    return os.path.join(base, name)


def _meta_path(repo_dir: str) -> str:
    return os.path.join(repo_dir, META_FILE)


def create_repo(name: str, namespace: str = "b", base: str = None,
                custom_path: str = None) -> dict:
    """新建仓库。

    Args:
        name: 仓库名
        namespace: b=企业自有, hq=总部共享库
        base: REPOS_BASE（通常不需要传）
        custom_path: 自定义磁盘路径。如果指定，仓库创建在该路径下，
                     同时在 REPOS_BASE 的注册表中记录映射。
    """
    base = base or REPOS_BASE
    repo_dir = _repo_disk_path(base, name, custom_path)

    if os.path.exists(repo_dir):
        raise FileExistsError(f"目录已存在: {repo_dir}")

    os.makedirs(os.path.join(repo_dir, ".dataproc"), exist_ok=True)
    ent_id = "hq" if namespace == "hq" else ("ent_" + uuid.uuid4().hex[:8])
    meta = {
        "name": name,
        "enterprise_id": ent_id,
        "namespace": namespace,
        "created_at": now_iso(),
        "disk_path": repo_dir,
    }
    with open(_meta_path(repo_dir), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    for tf in TOP_FOLDERS:
        os.makedirs(os.path.join(repo_dir, tf), exist_ok=True)

    # 注册到全局注册表（支持自定义路径的仓库被发现）
    reg = _load_registry(base)
    reg[name] = {"disk_path": repo_dir, **meta}
    _save_registry(reg, base)

    _set_current(base, name)
    return meta


def list_repos(base: str = None) -> List[dict]:
    """列举所有仓库（包括自定义路径的）。"""
    base = base or REPOS_BASE
    reg = _load_registry(base)
    out: List[dict] = []

    # 优先从注册表加载（包含自定义路径仓库）
    for name, info in sorted(reg.items()):
        disk_path = info.get("disk_path", os.path.join(base, name))
        mp = _meta_path(disk_path)
        if os.path.isfile(mp):
            with open(mp, encoding="utf-8") as f:
                m = json.load(f)
            if "disk_path" not in m:
                m["disk_path"] = disk_path
            out.append(m)

    # 兼容：扫描 REPOS_BASE 下未注册的仓库（旧格式）
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            if name in reg or name in (REGISTRY_FILE, CURRENT_FILE):
                continue
            mp = _meta_path(os.path.join(base, name))
            if os.path.isfile(mp):
                with open(mp, encoding="utf-8") as f:
                    m = json.load(f)
                m.setdefault("disk_path", os.path.join(base, name))
                out.append(m)

    return out


def get_repo(name: str, base: str = None) -> Tuple[str, dict]:
    """获取仓库目录路径和元数据。支持注册表中的自定义路径。"""
    base = base or REPOS_BASE

    # 先查注册表
    reg = _load_registry(base)
    if name in reg:
        disk_path = reg[name].get("disk_path", os.path.join(base, name))
        mp = _meta_path(disk_path)
        if os.path.isfile(mp):
            with open(mp, encoding="utf-8") as f:
                return disk_path, json.load(f)

    # 兼容：直接在 REPOS_BASE 下找
    repo_dir = os.path.join(base, name)
    mp = _meta_path(repo_dir)
    if os.path.isfile(mp):
        with open(mp, encoding="utf-8") as f:
            return repo_dir, json.load(f)

    raise FileNotFoundError(f"仓库不存在: {name}")


def _set_current(base: str, name: str) -> None:
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, CURRENT_FILE), "w", encoding="utf-8") as f:
        f.write(name)


def get_current(base: str = None) -> str:
    base = base or REPOS_BASE
    p = os.path.join(base, CURRENT_FILE)
    if os.path.isfile(p):
        return open(p, encoding="utf-8").read().strip()
    return None
