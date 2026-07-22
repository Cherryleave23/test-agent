"""仓库管理：新建 / 列举 / 读取 / 切换。仓库 = 磁盘目录，映射 enterprise_id + namespace。"""
import json
import os
import uuid
from typing import List, Tuple

from dataproc.repo import TOP_FOLDERS  # 复用引擎侧固定三类定义

from .config import REPOS_BASE
from .util import now_iso

META_FILE = ".dataproc/repo.json"
CURRENT_FILE = ".current"


def _repo_path(base: str, name: str) -> str:
    return os.path.join(base, name)


def _meta_path(repo_dir: str) -> str:
    return os.path.join(repo_dir, META_FILE)


def create_repo(name: str, namespace: str = "b", base: str = None) -> dict:
    base = base or REPOS_BASE
    repo_dir = _repo_path(base, name)
    if os.path.exists(repo_dir):
        raise FileExistsError(f"仓库已存在: {name}")
    os.makedirs(os.path.join(repo_dir, ".dataproc"), exist_ok=True)
    ent_id = "hq" if namespace == "hq" else ("ent_" + uuid.uuid4().hex[:8])
    meta = {
        "name": name,
        "enterprise_id": ent_id,
        "namespace": namespace,
        "created_at": now_iso(),
    }
    with open(_meta_path(repo_dir), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    for tf in TOP_FOLDERS:
        os.makedirs(os.path.join(repo_dir, tf), exist_ok=True)
    _set_current(base, name)
    return meta


def list_repos(base: str = None) -> List[dict]:
    base = base or REPOS_BASE
    out: List[dict] = []
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        mp = _meta_path(_repo_path(base, name))
        if os.path.isfile(mp):
            with open(mp, encoding="utf-8") as f:
                out.append(json.load(f))
    return out


def get_repo(name: str, base: str = None) -> Tuple[str, dict]:
    base = base or REPOS_BASE
    repo_dir = _repo_path(base, name)
    mp = _meta_path(repo_dir)
    if not os.path.isfile(mp):
        raise FileNotFoundError(f"仓库不存在: {name}")
    with open(mp, encoding="utf-8") as f:
        return repo_dir, json.load(f)


def _set_current(base: str, name: str) -> None:
    with open(os.path.join(base, CURRENT_FILE), "w", encoding="utf-8") as f:
        f.write(name)


def get_current(base: str = None) -> str:
    base = base or REPOS_BASE
    p = os.path.join(base, CURRENT_FILE)
    if os.path.isfile(p):
        return open(p, encoding="utf-8").read().strip()
    return None
