"""仓库元信息读取（引擎侧，独立于 GUI）。"""
import json
import os

# 固定三类总文件夹 → corpus kind（与产物契约一致）
TOP_FOLDERS = ["产品资料", "知识类文章", "原料资料"]
KIND_BY_TOP = {"产品资料": "product_text", "知识类文章": "article", "原料资料": "ingredient"}


def load_meta(repo_dir: str) -> dict:
    """读取 <repo>/.dataproc/repo.json（name/enterprise_id/namespace/created_at）。"""
    p = os.path.join(repo_dir, ".dataproc", "repo.json")
    if not os.path.isfile(p):
        raise FileNotFoundError(f"非法的 dataproc 仓库（缺少 .dataproc/repo.json）: {repo_dir}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)
