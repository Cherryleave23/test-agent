"""GUI 后端配置（仓库根目录）。

REPOS_BASE 现在是动态的（从 settings.json 读取），
通过 repos.get_repos_base() 获取实际路径。
此模块保留 REPOS_BASE 供向后兼容，但实际值通过 proxy 动态获取。
"""
from .repos import get_repos_base, REPOS_BASE  # noqa: F401
