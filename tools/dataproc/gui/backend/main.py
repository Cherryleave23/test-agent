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
from .models import RepoCreate, SettingsUpdate

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
    _save_gui_settings(cur)
    return {
        "ocr_enabled": cur.get("ocr_enabled", False),
        "run_real_ocr": cur.get("run_real_ocr", False),
        "output_dir": cur.get("output_dir", ""),
        "repos_base": repos.get_repos_base(),
    }


# SPA 兜底路由
if os.path.isdir(_DIST):

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        cand = os.path.join(_DIST, full_path)
        if full_path and os.path.isfile(cand):
            return FileResponse(cand)
        return FileResponse(os.path.join(_DIST, "index.html"))
