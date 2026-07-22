"""FastAPI 后端：GUI 工作台 REST 接口。复用 dataproc 引擎，零 import src.*。

新增端点：
  - POST /tree/mkdir: 新建子文件夹
  - POST /process: 支持 force（强制重处理）和 out_dir（自定义输出）
  - POST /markers/clear: 清除处理标记
  - GET/POST /settings: 读取/更新设置（OCR、输出目录等）
"""
import json
import os

from fastapi import FastAPI, Form, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse

from . import repos, tree, upload, markers, process as proc
from .models import RepoCreate, SettingsUpdate

app = FastAPI(title="dataproc GUI backend", version="0.2.0")

# 本地 Web 模式：构建后的前端 dist 存在时，由后端同源托管（见文件末尾 spa_fallback 兜底路由）。
_DIST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # .../gui
    "frontend", "dist",
)

# 设置文件路径（REPOS_BASE 下）
_SETTINGS_FILE = os.path.join(repos.REPOS_BASE, "settings.json")


def _load_settings() -> dict:
    if os.path.isfile(_SETTINGS_FILE):
        with open(_SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {
        "ocr_enabled": False,
        "run_real_ocr": False,
        "output_dir": "",
    }


def _save_settings(data: dict) -> None:
    os.makedirs(os.path.dirname(_SETTINGS_FILE), exist_ok=True)
    with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.get("/repos")
def get_repos():
    return {"repos": repos.list_repos(), "current": repos.get_current()}


@app.post("/repos")
def create_repo(body: RepoCreate):
    try:
        meta = repos.create_repo(body.name, body.namespace, custom_path=body.path)
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return meta


@app.post("/repos/switch")
def switch_repo(name: str = Form(...)):
    try:
        repos.get_repo(name)  # 校验存在
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    repos._set_current(repos.REPOS_BASE, name)
    return {"current": name}


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
    sel = json.loads(selection) if selection else None
    try:
        return proc.process(name, sel, force=(force.lower() == "true"),
                            out_dir=out_dir or None)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/bundle")
def get_bundle(name: str):
    repo_dir, _meta = repos.get_repo(name)
    bp = os.path.join(repo_dir, ".dataproc", "bundle", "manifest.json")
    if not os.path.isfile(bp):
        raise HTTPException(status_code=404, detail="尚未生成 bundle")
    with open(bp, encoding="utf-8") as f:
        return JSONResponse(json.load(f), media_type="application/json")


@app.get("/settings")
def get_settings():
    return _load_settings()


@app.post("/settings")
def update_settings(body: SettingsUpdate):
    cur = _load_settings()
    if body.ocr_enabled is not None:
        cur["ocr_enabled"] = body.ocr_enabled
    if body.run_real_ocr is not None:
        cur["run_real_ocr"] = body.run_real_ocr
    if body.output_dir is not None:
        cur["output_dir"] = body.output_dir
    _save_settings(cur)
    return cur


# SPA 兜底路由（必须最后注册）：API 路由已先注册并优先匹配，未命中才回退到
# 静态资源 / SPA index.html。仅本地 Web 模式（dist 存在）启用。
if os.path.isdir(_DIST):

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        cand = os.path.join(_DIST, full_path)
        if full_path and os.path.isfile(cand):
            return FileResponse(cand)
        return FileResponse(os.path.join(_DIST, "index.html"))
