"""FastAPI 后端：GUI 工作台 REST 接口。复用 dataproc 引擎，零 import src.*。

端点：
  - GET/POST /repos: 列举/创建仓库（支持自定义路径、已有目录初始化）
  - POST /repos/switch: 切换当前仓库
  - GET /tree: 树状列举
  - POST /tree/mkdir: 新建子文件夹
  - DELETE /tree/rmdir: 删除子文件夹
  - POST /upload: 拖拽上传文件
  - POST /process: 异步处理（后台线程，立即返回）
  - GET /process/status: 轮询处理进度（含实时日志）
  - GET /processed: 已处理标记列表
  - POST /markers/clear: 清除处理标记
  - GET /bundle: 获取最新 bundle manifest
  - GET/POST /settings: 读取/更新设置（OCR、输出目录、仓库根目录）
  - GET /repos/base: 获取当前仓库根目录
"""
import json
import logging
import os

from fastapi import FastAPI, Form, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse

from . import repos, tree, upload, markers, process as proc, progress
from .models import RepoCreate, SettingsUpdate, LLMSettings, SchemaUpdate, SchemaField, SchemaDef
from . import llm_client
import subprocess

# 配置日志：确保 info 级别日志输出到控制台
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(title="dataproc GUI backend", version="0.4.0")

_DIST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "frontend", "dist",
)


def _load_gui_settings() -> dict:
    """加载 GUI 设置（repos_base 等全局设置，存在默认 settings.json 中）。"""
    sp = os.path.join(repos._DEFAULT_BASE, repos.SETTINGS_FILE)
    if os.path.isfile(sp):
        try:
            with open(sp, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_gui_settings(data: dict) -> None:
    os.makedirs(repos._DEFAULT_BASE, exist_ok=True)
    sp = os.path.join(repos._DEFAULT_BASE, repos.SETTINGS_FILE)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.get("/repos")
def get_repos():
    return {"repos": repos.list_repos(), "current": repos.get_current()}


@app.post("/repos")
def create_repo(body: RepoCreate):
    try:
        meta = repos.create_repo(body.name, body.namespace,
                                 custom_path=body.path,
                                 output_dir=body.output_dir)
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return meta


@app.post("/repos/switch")
def switch_repo(name: str = Form(...)):
    try:
        repos.get_repo(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    repos._set_current(repos.get_repos_base(), name)
    return {"current": name}


@app.get("/repos/base")
def get_repos_base():
    """返回当前仓库根目录的实际磁盘路径。"""
    return {"repos_base": repos.get_repos_base()}


@app.get("/tree")
def get_tree(name: str, path: str = ""):
    try:
        return tree.list_tree(name, path)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/tree/full")
def get_tree_full(name: str):
    """递归返回仓库完整树（所有层级），供 Obsidian 风格前端渲染。"""
    try:
        return tree.list_tree_full(name)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/tree/mkdir")
def make_dir(name: str = Form(...), parent_path: str = Form(""), folder_name: str = Form(...)):
    try:
        return tree.mkdir(name, parent_path, folder_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/tree/rmdir")
def remove_dir(name: str = Form(...), folder_path: str = Form(...)):
    try:
        return tree.rmdir(name, folder_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/tree/file")
def remove_file(name: str = Form(...), file_path: str = Form(...)):
    try:
        return tree.delete_file(name, file_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/tree/move")
def move_item(name: str = Form(...), src_path: str = Form(...), dst_folder: str = Form("")):
    try:
        return tree.move(name, src_path, dst_folder)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/file_content")
def file_content(name: str, path: str):
    try:
        return tree.read_file(name, path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/open_explorer")
def open_explorer(name: str, path: str = ""):
    """在系统资源管理器中打开指定路径（文件夹或文件所在目录）。"""
    try:
        repo_dir, _meta = repos.get_repo(name)
        target = os.path.normpath(os.path.join(repo_dir, path)) if path else repo_dir
        if not (target == repo_dir or target.startswith(repo_dir + os.sep)):
            raise ValueError("非法路径（越界）")
        if not os.path.exists(target):
            raise FileNotFoundError("路径不存在")
        # 打开文件夹，或文件所在目录并选中文件
        if os.path.isfile(target):
            if os.name == "nt":
                subprocess.run(["explorer", "/select,", target])
            else:
                subprocess.run(["open", "-R", target])
        else:
            if os.name == "nt":
                subprocess.run(["explorer", target])
            else:
                subprocess.run(["open", target])
        return {"opened": target}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/upload")
async def upload_file(name: str = Form(...), folder: str = Form(""),
                      file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空（可能是文件夹项）")
    try:
        return upload.upload_file(name, folder, file.filename, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/processed")
def get_processed(name: str):
    return {"markers": markers.list_markers(name)}


@app.post("/markers/clear")
def clear_markers_ep(name: str = Form(...)):
    n = markers.clear_markers(name)
    return {"cleared": n}


@app.post("/process")
def do_process(name: str = Form(...), selection: str = Form(None),
               force: str = Form("false"), out_dir: str = Form("")):
    """异步处理：后台线程运行，立即返回。前端轮询 /process/status 获取进度。"""
    sel = json.loads(selection) if selection else None
    try:
        return proc.process_async(name, sel, force=(force.lower() == "true"),
                                   out_dir=out_dir or None)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/process/status")
def get_process_status():
    """轮询处理进度。返回 status/total/processed/current_file/logs 等。"""
    return progress.get()


@app.get("/bundle")
def get_bundle(name: str):
    repo_dir, meta = repos.get_repo(name)

    # 检查输出目录：优先级 仓库级 output_dir > 全局 settings.output_dir > 默认位置
    repo_out = meta.get("output_dir") or ""
    if repo_out:
        bp = os.path.join(repo_out, "manifest.json")
        if os.path.isfile(bp):
            with open(bp, encoding="utf-8") as f:
                return JSONResponse(json.load(f), media_type="application/json")

    s = _load_gui_settings()
    out_dir = s.get("output_dir") or ""
    if out_dir:
        bp = os.path.join(out_dir, "manifest.json")
        if os.path.isfile(bp):
            with open(bp, encoding="utf-8") as f:
                return JSONResponse(json.load(f), media_type="application/json")

    # 默认位置
    bp = os.path.join(repo_dir, ".dataproc", "bundle", "manifest.json")
    if not os.path.isfile(bp):
        raise HTTPException(status_code=404, detail="尚未生成 bundle，请先点击处理")
    with open(bp, encoding="utf-8") as f:
        return JSONResponse(json.load(f), media_type="application/json")


@app.get("/settings")
def get_settings():
    s = _load_gui_settings()
    return {
        "ocr_enabled": s.get("ocr_enabled", False),
        "run_real_ocr": s.get("run_real_ocr", False),
        "output_dir": s.get("output_dir", ""),
        "repos_base": repos.get_repos_base(),
    }


@app.post("/settings")
def update_settings(body: SettingsUpdate):
    cur = _load_gui_settings()
    if body.ocr_enabled is not None:
        cur["ocr_enabled"] = body.ocr_enabled
    if body.run_real_ocr is not None:
        cur["run_real_ocr"] = body.run_real_ocr
    if body.output_dir is not None:
        cur["output_dir"] = body.output_dir
    if body.repos_base is not None:
        repos.set_repos_base(body.repos_base)
        cur["repos_base"] = body.repos_base
    if body.llm is not None:
        cur["llm"] = body.llm.model_dump()
    _save_gui_settings(cur)
    return {
        "ocr_enabled": cur.get("ocr_enabled", False),
        "run_real_ocr": cur.get("run_real_ocr", False),
        "output_dir": cur.get("output_dir", ""),
        "repos_base": repos.get_repos_base(),
        "llm": cur.get("llm", {}),
    }


# ---- LLM 配置页（独立端点） ----
def _load_llm_settings() -> dict:
    """从 settings.json 读 llm 块，缺省回落 DEFAULTS。api_key 脱敏。"""
    from dataproc.config import DEFAULTS
    s = _load_gui_settings()
    llm = dict(DEFAULTS.get("llm", {}))
    llm.update(s.get("llm", {}) or {})
    # 脱敏
    if llm.get("api_key"):
        llm["api_key"] = "<set>"
    return llm


@app.get("/settings/llm")
def get_llm_settings():
    return _load_llm_settings()


@app.post("/settings/llm")
def update_llm_settings(body: LLMSettings):
    cur = _load_gui_settings()
    llm = body.model_dump()
    # 如果 api_key 是脱敏值 "<set>"，保留原有 api_key（用户未重新输入）
    if llm.get("api_key") == "<set>":
        old_llm = cur.get("llm", {})
        llm["api_key"] = old_llm.get("api_key", "")
    cur["llm"] = llm
    _save_gui_settings(cur)
    out = dict(llm)
    if out.get("api_key"):
        out["api_key"] = "<set>"
    return out


@app.post("/settings/llm/test")
def test_llm_settings(body: LLMSettings):
    """真实连通测试：复用 llm_client.test_connection。"""
    cfg = body.model_dump()
    try:
        result = llm_client.test_connection(cfg)
    except Exception as e:
        result = {"ok": False, "latency_ms": 0, "models": [],
                  "endpoint": "", "error": f"{type(e).__name__}: {e}",
                  "kind": cfg.get("kind", "none")}
    return result


# ---- 产品数据结构（schema）配置页 ----
def _normalize_schemas_for_ui(schemas: dict) -> dict:
    """把合并后的 {name: SchemaDef} 规整为前端友好的结构（标注 builtin）。"""
    from dataproc.schema_conf import _builtin_names
    builtin = _builtin_names()
    out: dict = {}
    for name, spec in schemas.items():
        out[name] = {
            "label": spec.get("label", name),
            "kind": spec.get("kind", "flex"),
            "extends": spec.get("extends"),
            "keywords": spec.get("keywords", []) or [],
            "fields": spec.get("fields", []),
            "builtin": name in builtin,
        }
    return out


@app.get("/settings/schema")
def get_schema_settings():
    """返回当前产品数据结构（内置默认 + conf.yaml 自定义类目）。"""
    from dataproc.schema_conf import load_schemas
    return {"schemas": _normalize_schemas_for_ui(load_schemas())}


@app.post("/settings/schema")
def update_schema_settings(body: SchemaUpdate):
    """写入 conf.yaml 的 product_schemas 段（企业自定义类目/字段）；保留其余段。

    校验：schema 名与字段 key 须为字母/数字/下划线、非空。
    """
    import re as _re
    _key_pat = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    cleaned: dict = {}
    for name, spec in (body.schemas or {}).items():
        if not _key_pat.match(str(name)):
            raise HTTPException(status_code=400,
                                detail=f"类目名非法（字母/数字/下划线）：{name}")
        if isinstance(spec, SchemaDef):
            spec = spec.model_dump()
        fields = []
        for fd in (spec.get("fields") or []):
            if isinstance(fd, SchemaField):
                fd = fd.model_dump()
            key = (fd.get("key") or "").strip()
            if not key or not _key_pat.match(key):
                raise HTTPException(status_code=400,
                                    detail=f"字段 key 非法：{key}")
            fields.append({
                "key": key,
                "label": fd.get("label") or key,
                "type": fd.get("type") or "text",
                "required": bool(fd.get("required", False)),
                "aliases": fd.get("aliases") or [],
            })
        cleaned[str(name)] = {
            "label": spec.get("label") or str(name),
            "kind": spec.get("kind") or "flex",
            "extends": spec.get("extends"),
            "keywords": spec.get("keywords") or [],
            "fields": fields,
        }
    from dataproc.schema_conf import write_product_schemas
    try:
        write_product_schemas(cleaned)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入 conf.yaml 失败：{e}")
    from dataproc.schema_conf import load_schemas
    return {"schemas": _normalize_schemas_for_ui(load_schemas())}


# SPA 兜底路由
if os.path.isdir(_DIST):

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        cand = os.path.join(_DIST, full_path)
        if full_path and os.path.isfile(cand):
            return FileResponse(cand)
        return FileResponse(os.path.join(_DIST, "index.html"))
