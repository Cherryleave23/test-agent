"""仓库元信息读取与初始化（引擎侧，独立于 GUI）。"""
import json
import os
import uuid
from datetime import datetime, timezone

# 固定三类总文件夹 → corpus kind（与产物契约一致）
TOP_FOLDERS = ["产品资料", "知识类文章", "原料资料"]
KIND_BY_TOP = {"产品资料": "product_text", "知识类文章": "article", "原料资料": "ingredient"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def init_repo(repo_dir: str, name: str, namespace: str = "b",
              output_dir: str = None) -> dict:
    """在 *已有或新建* 的 disk 路径上初始化一个 dataproc 仓库。

    与 GUI 侧 `repos.create_repo` 写入相同的 repo.json 契约
    （name/enterprise_id/namespace/created_at），但不依赖任何 GUI 模块，
    供引擎侧脚本 / 验收 harness 直接调用。

    Args:
        repo_dir: 仓库磁盘根目录（可不存在，会自动 makedirs）。
        name: 仓库名（展示用）。
        namespace: b=企业自有, hq=总部共享库。
        output_dir: 每仓库独立 bundle 输出目录（可选）。

    Returns: 写入的 meta dict。
    """
    os.makedirs(repo_dir, exist_ok=True)
    os.makedirs(os.path.join(repo_dir, ".dataproc"), exist_ok=True)
    ent_id = "hq" if namespace == "hq" else ("ent_" + uuid.uuid4().hex[:8])
    meta = {
        "name": name,
        "enterprise_id": ent_id,
        "namespace": namespace,
        "created_at": _now_iso(),
        "disk_path": repo_dir,
    }
    if output_dir:
        meta["output_dir"] = output_dir
    with open(os.path.join(repo_dir, ".dataproc", "repo.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    for tf in TOP_FOLDERS:
        os.makedirs(os.path.join(repo_dir, tf), exist_ok=True)
    return meta


def load_meta(repo_dir: str) -> dict:
    """读取 <repo>/.dataproc/repo.json（name/enterprise_id/namespace/created_at）。"""
    p = os.path.join(repo_dir, ".dataproc", "repo.json")
    if not os.path.isfile(p):
        raise FileNotFoundError(f"非法的 dataproc 仓库（缺少 .dataproc/repo.json）: {repo_dir}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)
