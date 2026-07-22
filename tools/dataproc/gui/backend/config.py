"""GUI 后端配置（仓库根目录）。测试时可直接覆盖 REPOS_BASE。"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../gui/backend
_GUI = os.path.dirname(_HERE)                               # .../gui

# 默认仓库根：<tools>/dataproc/gui/repos（运行时可用环境变量覆盖）
REPOS_BASE = os.environ.get("DATAPROC_REPOS_BASE") or os.path.join(_GUI, "repos")
