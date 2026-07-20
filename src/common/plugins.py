"""插件管理器：重型可选依赖的发现、安装、校验、路径解析。

设计目标：
  - 安装包保持轻量（~80MB），torch/模型等重型依赖在配置阶段按需拉取
  - 模型可插拔替换：bge-small-zh-v1.5 可换成其他嵌入模型，不动业务代码
  - 多来源安装：URL 下载 / 本地文件导入 / HuggingFace 镜像 / pip 安装
  - SHA256 校验防篡改

插件类型：
  - runtime: Python 包（torch, sentence-transformers），通过 pip 安装
  - model: 模型文件（bge-small-zh-v1.5, bge-reranker-v2-m3），解压到 plugins/models/

使用方式：
  # 检查插件是否已安装
  pm = PluginManager()
  pm.is_installed("bge-small-zh-v1.5")  # → False

  # 获取模型路径（未安装返回 None）
  path = pm.model_path("bge-small-zh-v1.5")  # → Path 或 None

  # 列出已安装插件
  pm.list_installed()  # → ["torch-cpu", "sentence-transformers"]

  # 解析预设
  pm.preset_plugins("full")  # → ["torch-cpu", "sentence-transformers", ...]
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# 插件根目录：相对于项目根（src/ 的上级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PLUGINS_DIR = _PROJECT_ROOT / "plugins"
_MANIFEST_PATH = _PLUGINS_DIR / "manifest.yaml"
_INSTALLED_LOCK = _PLUGINS_DIR / "installed.lock"


@dataclass
class PluginSource:
    """插件安装来源。"""
    kind: str  # url | pip | huggingface | local
    url: str = ""
    sha256: str = ""
    spec: str = ""  # pip install spec
    repo: str = ""  # HuggingFace repo id


@dataclass
class Plugin:
    """插件声明。"""
    id: str
    type: str  # runtime | model
    description: str = ""
    size_hint: str = ""
    sources: List[PluginSource] = field(default_factory=list)
    install_path: str = ""
    depends_on: List[str] = field(default_factory=list)
    requires_python: str = ""


class PluginManager:
    """插件管理器：读取 manifest.yaml，管理插件安装状态。

    安装状态记录在 plugins/installed.lock（JSON），记录已安装插件 id + 来源 + 时间。
    模型文件安装在 plugins/models/<plugin_id>/ 目录。
    运行时插件（torch 等）通过 pip 安装到当前 Python 环境。
    """

    def __init__(self, manifest_path: Optional[Path] = None,
                 plugins_dir: Optional[Path] = None):
        self.manifest_path = manifest_path or _MANIFEST_PATH
        self.plugins_dir = plugins_dir or _PLUGINS_DIR
        self._manifest: Optional[dict] = None
        self._plugins: Optional[Dict[str, Plugin]] = None
        self._installed: Optional[Dict[str, dict]] = None

    # ── 清单加载 ──

    @property
    def manifest(self) -> dict:
        if self._manifest is None:
            if not self.manifest_path.exists():
                self._manifest = {"plugins": [], "presets": {}}
            else:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    self._manifest = yaml.safe_load(f) or {}
        return self._manifest

    @property
    def plugins(self) -> Dict[str, Plugin]:
        if self._plugins is None:
            self._plugins = {}
            for raw in self.manifest.get("plugins", []):
                sources = []
                for s in raw.get("sources", []):
                    sources.append(PluginSource(
                        kind=s.get("kind", ""),
                        url=s.get("url", ""),
                        sha256=s.get("sha256", ""),
                        spec=s.get("spec", ""),
                        repo=s.get("repo", ""),
                    ))
                p = Plugin(
                    id=raw["id"],
                    type=raw.get("type", ""),
                    description=raw.get("description", ""),
                    size_hint=raw.get("size_hint", ""),
                    sources=sources,
                    install_path=raw.get("install_path", ""),
                    depends_on=raw.get("depends_on", []),
                    requires_python=raw.get("requires_python", ""),
                )
                self._plugins[p.id] = p
        return self._plugins

    def preset_plugins(self, preset: str) -> List[str]:
        """返回预设包含的插件 id 列表。"""
        presets = self.manifest.get("presets", {})
        if preset not in presets:
            return []
        return presets[preset].get("plugins", [])

    # ── 安装状态 ──

    @property
    def installed(self) -> Dict[str, dict]:
        if self._installed is None:
            if _INSTALLED_LOCK.exists():
                with open(_INSTALLED_LOCK, "r", encoding="utf-8") as f:
                    self._installed = json.load(f)
            else:
                self._installed = {}
        return self._installed

    def is_installed(self, plugin_id: str) -> bool:
        return plugin_id in self.installed

    def list_installed(self) -> List[str]:
        return list(self.installed.keys())

    def _mark_installed(self, plugin_id: str, source: str) -> None:
        from datetime import datetime
        self.installed[plugin_id] = {
            "source": source,
            "installed_at": datetime.now().isoformat(),
        }
        self._save_lock()

    def _save_lock(self) -> None:
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        with open(_INSTALLED_LOCK, "w", encoding="utf-8") as f:
            json.dump(self.installed, f, indent=2, ensure_ascii=False)

    # ── 模型路径解析（业务代码用）──

    def model_path(self, plugin_id: str) -> Optional[Path]:
        """返回模型插件的安装路径，未安装返回 None。

        embeddings.py / rerank.py 通过此方法获取模型路径，
        实现可插拔：模型路径来自插件系统，而非硬编码 HuggingFace repo。
        """
        if not self.is_installed(plugin_id):
            return None
        p = self.plugins.get(plugin_id)
        if p is None or p.type != "model":
            return None
        path = self.plugins_dir / p.install_path
        return path if path.exists() else None

    def resolve_model(self, model_kind: str) -> Optional[str]:
        """按 embedding/rerank kind 解析模型路径或 repo id。

        优先级：已安装的本地插件路径 > HuggingFace repo id > None
        embeddings.py 调用此方法，实现「本地插件优先，无则回退 HF 下载」。
        """
        # 映射：embedding kind → 插件 id
        kind_map = {
            "bge": "bge-small-zh-v1.5",
            "bge-small-zh": "bge-small-zh-v1.5",
            "bge-small-zh-v1.5": "bge-small-zh-v1.5",
        }
        rerank_map = {
            "bge-reranker-v2-m3": "bge-reranker-v2-m3",
        }
        plugin_id = kind_map.get(model_kind) or rerank_map.get(model_kind)
        if plugin_id is None:
            return None

        # 优先返回本地插件路径
        local_path = self.model_path(plugin_id)
        if local_path:
            return str(local_path)

        # 未安装本地插件 → 返回 HuggingFace repo id（sentence-transformers 会自动下载）
        p = self.plugins.get(plugin_id)
        if p:
            for s in p.sources:
                if s.kind == "huggingface" and s.repo:
                    return s.repo
        return None

    # ── 安装操作（configure.ps1 调用）──

    def install_from_url(self, plugin_id: str, url: str,
                         sha256: str = "") -> bool:
        """从 URL 下载并安装插件。

        runtime 类型：下载 .whl 后 pip install
        model 类型：下载压缩包后解压到 install_path
        """
        import urllib.request
        import tempfile
        import zipfile

        p = self.plugins.get(plugin_id)
        if p is None:
            return False

        print(f"[插件] 下载 {plugin_id} from {url} ...")
        tmp = Path(tempfile.mkdtemp())
        try:
            filename = url.split("/")[-1].split("?")[0]
            target = tmp / filename
            urllib.request.urlretrieve(url, target)

            # SHA256 校验
            if sha256:
                actual = self._sha256(target)
                if actual != sha256:
                    print(f"[插件] 校验失败: {actual} != {sha256}")
                    return False
                print(f"[插件] SHA256 校验通过")

            if p.type == "runtime":
                # pip install <wheel>
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", str(target)]
                )
            elif p.type == "model":
                # 解压到 install_path
                dest = self.plugins_dir / p.install_path
                dest.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(target, "r") as zf:
                    zf.extractall(dest)

            self._mark_installed(plugin_id, f"url:{url}")
            print(f"[插件] {plugin_id} 安装成功")
            return True
        except Exception as e:
            print(f"[插件] {plugin_id} 安装失败: {e}")
            return False
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def install_from_local(self, plugin_id: str, file_path: str) -> bool:
        """从本地文件导入插件（USB / 本地磁盘）。

        runtime 类型：.whl 文件 → pip install
        model 类型：.zip 文件 → 解压到 install_path
        """
        p = self.plugins.get(plugin_id)
        if p is None:
            return False

        src = Path(file_path)
        if not src.exists():
            print(f"[插件] 文件不存在: {file_path}")
            return False

        print(f"[插件] 从本地导入 {plugin_id}: {file_path}")
        try:
            if p.type == "runtime":
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", str(src)]
                )
            elif p.type == "model":
                dest = self.plugins_dir / p.install_path
                dest.mkdir(parents=True, exist_ok=True)
                import zipfile
                with zipfile.ZipFile(src, "r") as zf:
                    zf.extractall(dest)

            self._mark_installed(plugin_id, f"local:{file_path}")
            print(f"[插件] {plugin_id} 导入成功")
            return True
        except Exception as e:
            print(f"[插件] {plugin_id} 导入失败: {e}")
            return False

    def install_from_pip(self, plugin_id: str) -> bool:
        """通过 pip 安装运行时插件。"""
        p = self.plugins.get(plugin_id)
        if p is None or p.type != "runtime":
            return False

        for s in p.sources:
            if s.kind == "pip" and s.spec:
                print(f"[插件] pip install {s.spec}")
                try:
                    # spec 可能含 --index-url 等参数，需要拆分
                    parts = s.spec.split()
                    subprocess.check_call(
                        [sys.executable, "-m", "pip", "install"] + parts
                    )
                    self._mark_installed(plugin_id, f"pip:{s.spec}")
                    print(f"[插件] {plugin_id} pip 安装成功")
                    return True
                except Exception as e:
                    print(f"[插件] {plugin_id} pip 安装失败: {e}")
                    continue
        return False

    def install_plugin(self, plugin_id: str,
                       source_hint: str = "") -> bool:
        """按来源优先级尝试安装插件。"""
        p = self.plugins.get(plugin_id)
        if p is None:
            print(f"[插件] 未知插件: {plugin_id}")
            return False

        if self.is_installed(plugin_id):
            print(f"[插件] {plugin_id} 已安装，跳过")
            return True

        # 安装依赖
        for dep in p.depends_on:
            if not self.is_installed(dep):
                if not self.install_plugin(dep):
                    print(f"[插件] 依赖 {dep} 安装失败，中止")
                    return False

        # 按来源优先级尝试
        if source_hint == "local":
            # 交互式：由 configure.ps1 传入本地文件路径
            # 此方法不直接处理 local（需要文件路径），返回 False 让调用方处理
            return False

        for s in p.sources:
            if s.kind == "pip":
                if self.install_from_pip(plugin_id):
                    return True
            elif s.kind == "url" and s.url:
                if self.install_from_url(plugin_id, s.url, s.sha256):
                    return True
            # huggingface / local 类型由 configure.ps1 交互式处理

        return False

    def uninstall(self, plugin_id: str) -> bool:
        """卸载插件（删除记录 + 模型文件；runtime 不 pip uninstall）。"""
        p = self.plugins.get(plugin_id)
        if p is None:
            return False
        if p.type == "model" and p.install_path:
            dest = self.plugins_dir / p.install_path
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
        if plugin_id in self.installed:
            del self.installed[plugin_id]
            self._save_lock()
        print(f"[插件] {plugin_id} 已卸载")
        return True

    # ── 工具方法 ──

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
