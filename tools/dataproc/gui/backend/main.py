"""FastAPI 后端：GUI 工作台 REST 接口。复用 dataproc 引擎，零 import src.*。"""
import json
import os

from fastapi import FastAPI, Form, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse

from . import repos, tree, upload, markers, process as proc
from .models import RepoCreate

app = FastAPI(title="dataproc GUI backend", version="0.1.0")


@app.get("/repos")
def get_repos():
    return {"repos": repos.list_repos(), "current": repos.get_current()}


@app.post("/repos")
def create_repo(body: RepoCreate):
    try:
        meta = repos.create_repo(body.name, body.namespace)
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
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


@app.post("/upload")
async def upload_file(name: str = Form(...), folder: str = Form(""),
                      file: UploadFile = File(...)):
    data = await file.read()
    try:
        return upload.upload_file(name, folder, file.filename, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/processed")
def get_processed(name: str):
    return {"markers": markers.list_markers(name)}


@app.post("/process")
def do_process(name: str = Form(...), selection: str = Form(None)):
    sel = json.loads(selection) if selection else None
    try:
        return proc.process(name, sel)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/bundle")
def get_bundle(name: str):
    repo_dir, _meta = repos.get_repo(name)
    bp = os.path.join(repo_dir, ".dataproc", "bundle", "manifest.json")
    if not os.path.isfile(bp):
        raise HTTPException(status_code=404, detail="尚未生成 bundle")
    with open(bp, encoding="utf-8") as f:
        return JSONResponse(json.load(f), media_type="application/json")
