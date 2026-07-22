"""仓库管理：新建 / 列举 / 读取 / 切换。仓库 = 磁盘目录，映射 enterprise_id + namespace。

支持：
- 自定义磁盘路径（用户可在创建仓库时指定磁盘位置）
- 已有目录初始化为仓库（不报错，补建 .dataproc 和三大总文件夹）
- 仓库注册表持久化（repos.json），记录所有仓库的 name→disk_path 映射
- REPOS_BASE 持久化到 settings.json，用户可在设置页面修改默认存储位置
"""
import json
import os
import uuid
from typing import List, Tuple, Optional

from dataproc.repo import TOP_FOLDERS  # 复用引擎侧固定三类定义

from .util import now_iso

META_FILE = ".dataproc/repo.json"
CURRENT_FILE = ".current"
REGISTRY_FILE = "repos.json"
SETTINGS_FILE = "settings.json"

# 默认仓库根：<tools>/dataproc/gui/repos
_HERE = os.path.dirname(os.path.abspath(__file__))
_GUI = os.path.dirname(_HERE)
_DEFAULT_BASE = os.path.join(_GUI, "repos")


def _settings_path(base: str) -> str:
    return os.path.join(base, SETTINGS_FILE)


# 模块级覆盖（测试可直接赋值，优先级最高）
_override_base: str = ""


def get_repos_base() -> str:
    """获取当前 REPOS_BASE。优先级：模块覆盖 > 环境变量 > settings.json > 默认路径。"""
    if _override_base:
        return _override_base
    env = os.environ.get("DATAPROC_REPOS_BASE")
    if env:
        return env
    # 从默认路径的 settings.json 读取用户设定的 repos_base
    default_settings = _settings_path(_DEFAULT_BASE)
    if os.path.isfile(default_settings):
        try:
            with open(default_settings, encoding="utf-8") as f:
                s = json.load(f)
            if s.get("repos_base"):
                return s["repos_base"]
        except Exception:
            pass
    return _DEFAULT_BASE


def set_repos_base(path: str) -> None:
    """持久化 REPOS_BASE 到 settings.json。"""
    os.makedirs(_DEFAULT_BASE, exist_ok=True)
    sp = _settings_path(_DEFAULT_BASE)
    s = {}
    if os.path.isfile(sp):
        try:
            with open(sp, encoding="utf-8") as f:
                s = json.load(f)
        except Exception:
            pass
    s["repos_base"] = path
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


# 兼容：其他模块通过 REPOS_BASE 访问（动态读取 get_repos_base()）
# 测试可直接 repos._override_base = tmp 来覆盖
class _ReposBaseProxy(str):
    """可变字符串代理：每次操作时动态返回 get_repos_base() 的值。"""
    def __new__(cls):
        return super().__new__(cls, get_repos_base())
    def __str__(self):
        return get_repos_base()
    def __fspath__(self):
        return get_repos_base()
    def __eq__(self, other):
        return get_repos_base() == other
    def __ne__(self, other):
        return get_repos_base() != other
    def __hash__(self):
        return hash(get_repos_base())
    def __repr__(self):
        return repr(get_repos_base())
    def __len__(self):
        return len(get_repos_base())
    def __getitem__(self, i):
        return get_repos_base()[i]
    def __add__(self, other):
        return get_repos_base() + other
    def __radd__(self, other):
        return other + get_repos_base()
    def __contains__(self, item):
        return item in get_repos_base()
    def join(self, iterable):
        return get_repos_base().join(iterable)
    def startswith(self, prefix, *args, **kwargs):
        return get_repos_base().startswith(prefix, *args, **kwargs)
    def endswith(self, suffix, *args, **kwargs):
        return get_repos_base().endswith(suffix, *args, **kwargs)
    def replace(self, *args, **kwargs):
        return get_repos_base().replace(*args, **kwargs)
    def split(self, *args, **kwargs):
        return get_repos_base().split(*args, **kwargs)
    def strip(self, *args):
        return get_repos_base().strip(*args)


REPOS_BASE = _ReposBaseProxy()


def _ensure_base() -> str:
    """确保 REPOS_BASE 目录存在并返回其路径。"""
    base = get_repos_base()
    os.makedirs(base, exist_ok=True)
    return base


def _registry_path(base: str) -> str:
    return os.path.join(base, REGISTRY_FILE)


def _load_registry(base: str = None) -> dict:
    """加载仓库注册表 name→{disk_path, ...meta}。"""
    base = base or get_repos_base()
    p = _registry_path(base)
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_registry(reg: dict, base: str = None) -> None:
    base = base or get_repos_base()
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
                custom_path: str = None, output_dir: str = None) -> dict:
    """新建仓库或初始化已有目录为仓库。

    Args:
        name: 仓库名
        namespace: b=企业自有, hq=总部共享库
        base: REPOS_BASE（通常不需要传）
        custom_path: 自定义磁盘路径。如果指定且目录已存在，则初始化为仓库
                     （补建 .dataproc 和三大总文件夹），不报错。
        output_dir: 每仓库独立输出目录。如果指定，bundle 产物将写入该目录
                    而非全局 settings.output_dir 或仓库内 .dataproc/bundle。
    """
    base = base or get_repos_base()
    repo_dir = _repo_disk_path(base, name, custom_path)

    # 已有目录：检查是否已是仓库
    if os.path.exists(repo_dir):
        mp = _meta_path(repo_dir)
        if os.path.isfile(mp):
            raise FileExistsError(f"该目录已是仓库: {repo_dir}")
        # 目录存在但不是仓库 → 初始化为仓库（补建结构）
    else:
        os.makedirs(repo_dir, exist_ok=True)

    os.makedirs(os.path.join(repo_dir, ".dataproc"), exist_ok=True)
    ent_id = "hq" if namespace == "hq" else ("ent_" + uuid.uuid4().hex[:8])
    meta = {
        "name": name,
        "enterprise_id": ent_id,
        "namespace": namespace,
        "created_at": now_iso(),
        "disk_path": repo_dir,
    }
    if output_dir:
        meta["output_dir"] = output_dir
    with open(_meta_path(repo_dir), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    for tf in TOP_FOLDERS:
        os.makedirs(os.path.join(repo_dir, tf), exist_ok=True)

    # 注册到全局注册表
    _ensure_base()
    reg = _load_registry(base)
    reg[name] = {"disk_path": repo_dir, **meta}
    _save_registry(reg, base)

    _set_current(base, name)
    return meta


def list_repos(base: str = None) -> List[dict]:
    """列举所有仓库（包括自定义路径的）。"""
    base = base or get_repos_base()
    _ensure_base()
    reg = _load_registry(base)
    out: List[dict] = []

    for name, info in sorted(reg.items()):
        disk_path = info.get("disk_path", os.path.join(base, name))
        mp = _meta_path(disk_path)
        if os.path.isfile(mp):
            with open(mp, encoding="utf-8") as f:
                m = json.load(f)
            m["disk_path"] = disk_path
            out.append(m)

    # 兼容：扫描 REPOS_BASE 下未注册的仓库
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            if name in reg or name in (REGISTRY_FILE, CURRENT_FILE, SETTINGS_FILE):
                continue
            mp = _meta_path(os.path.join(base, name))
            if os.path.isfile(mp):
                with open(mp, encoding="utf-8") as f:
                    m = json.load(f)
                m.setdefault("disk_path", os.path.join(base, name))
                out.append(m)

    return out


def get_repo(name: str, base: str = None) -> Tuple[str, dict]:
    """获取仓库目录路径和元数据。"""
    base = base or get_repos_base()
    reg = _load_registry(base)
    if name in reg:
        disk_path = reg[name].get("disk_path", os.path.join(base, name))
        mp = _meta_path(disk_path)
        if os.path.isfile(mp):
            with open(mp, encoding="utf-8") as f:
                return disk_path, json.load(f)

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
    base = base or get_repos_base()
    p = os.path.join(base, CURRENT_FILE)
    if os.path.isfile(p):
        return open(p, encoding="utf-8").read().strip()
    return None
