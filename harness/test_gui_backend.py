#!/usr/bin/env python3
# @module ingest
"""GUI 工作台后端真实运行验收（MOD-knowledge-ingest P5）。

用 FastAPI TestClient 驱动真实后端代码路径，断言：
  G1 仓库：新建/列举/切换（repo.json 落 enterprise_id+namespace）
  G2 树：固定三类顶层 + 多层嵌套（产品资料/奶粉/伊利/星飞帆/星飞帆1段800g）
  G3 上传：拖拽文件落到当前文件夹、不破坏结构
  G4 标记去重：同文件哈希未变二次处理跳过（计数 0）
  G5 触发：全量处理产出 NDJSON bundle（manifest 计数 + corpus_by_kind 正确）

直接运行：python3 test_gui_backend.py  → 退出码 0 全过，非 0 有失败。
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))  # 使 import dataproc 可用

from dataproc.gui.backend import repos  # noqa: E402
from dataproc.gui.backend.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


PRODUCT_MD = """---
brand: 伊利
series: 星飞帆
stage: 1段
reg_number: 国食注字YP20240001
manufacturer: 伊利营养品有限公司
---

星飞帆1段800g（新国标）产品说明：含OPO结构脂与A2蛋白。
"""

ARTICLE_MD = "# 新生儿睡眠\n新生儿睡眠周期短，按需喂养更安稳。\n"
INGREDIENT_MD = "# DHA\nDHA 促进婴幼儿脑发育，有效原因是…\n"


def seed_repo(repo_dir):
    nested = os.path.join(repo_dir, "产品资料", "奶粉", "伊利", "星飞帆", "星飞帆1段800g（新国标）")
    os.makedirs(nested, exist_ok=True)
    with open(os.path.join(nested, "info.md"), "w", encoding="utf-8") as f:
        f.write(PRODUCT_MD)
    os.makedirs(os.path.join(repo_dir, "知识类文章"), exist_ok=True)
    with open(os.path.join(repo_dir, "知识类文章", "睡眠.md"), "w", encoding="utf-8") as f:
        f.write(ARTICLE_MD)
    os.makedirs(os.path.join(repo_dir, "原料资料"), exist_ok=True)
    with open(os.path.join(repo_dir, "原料资料", "DHA.md"), "w", encoding="utf-8") as f:
        f.write(INGREDIENT_MD)


def main():
    fails = []
    tmp = tempfile.mkdtemp(prefix="dataproc_gui_")
    repos._override_base = tmp
    client = TestClient(app)
    try:
        # G1 仓库
        r = client.post("/repos", json={"name": "企业A", "namespace": "b"})
        if r.status_code != 200:
            fails.append(f"G1: 新建仓库失败 {r.status_code} {r.text}")
        else:
            meta = r.json()
            if not (meta.get("enterprise_id") and meta.get("namespace") == "b"):
                fails.append(f"G1: repo.json 缺 enterprise_id/namespace {meta}")
        lst = client.get("/repos").json()
        if not any(x["name"] == "企业A" for x in lst["repos"]):
            fails.append("G1: 列举仓库不含 企业A")
        sw = client.post("/repos/switch", data={"name": "企业A"})
        if sw.json().get("current") != "企业A":
            fails.append("G1: 切换仓库失败")

        # 准备资料（多层嵌套 + 文章 + 原料）
        repo_dir, _ = repos.get_repo("企业A", tmp)
        seed_repo(repo_dir)

        # G2 树：固定三类顶层 + 多层嵌套
        root = client.get("/tree", params={"name": "企业A"}).json()
        top_names = {f["name"] for f in root["folders"]}
        if top_names != {"产品资料", "知识类文章", "原料资料"}:
            fails.append(f"G2: 顶层三类不符 {top_names}")
        p = client.get("/tree", params={"name": "企业A", "path": "产品资料"}).json()
        if "奶粉" not in {f["name"] for f in p["folders"]}:
            fails.append("G2: 产品资料下缺 奶粉")
        nest = client.get("/tree", params={"name": "企业A",
                                            "path": "产品资料/奶粉/伊利/星飞帆"}).json()
        if "星飞帆1段800g（新国标）" not in {f["name"] for f in nest["folders"]}:
            fails.append("G2: 多层嵌套末级缺失")
        leaf = client.get("/tree", params={"name": "企业A",
                                           "path": "产品资料/奶粉/伊利/星飞帆/星飞帆1段800g（新国标）"}).json()
        if "info.md" not in {f["name"] for f in leaf["files"]}:
            fails.append("G2: 末级文件缺失 info.md")

        # G3 上传到当前文件夹（产品资料/奶粉）
        up = client.post("/upload",
                         data={"name": "企业A", "folder": "产品资料/奶粉"},
                         files={"file": ("extra.md", "# 额外产品资料\n补充说明。\n".encode("utf-8"), "text/plain")})
        if up.status_code != 200:
            fails.append(f"G3: 上传失败 {up.status_code} {up.text}")
        else:
            p2 = client.get("/tree", params={"name": "企业A", "path": "产品资料/奶粉"}).json()
            if "extra.md" not in {f["name"] for f in p2["files"]}:
                fails.append("G3: 上传文件未落到 产品资料/奶粉")

        # G4 + G5：首次全量处理（异步，需轮询等待完成）
        resp1 = client.post("/process", data={"name": "企业A"}).json()
        pr1 = _wait_process(client, resp1)
        n1 = pr1.get("processed", 0)
        if n1 != 4:
            fails.append(f"G4/G5: 首次处理文件数应为4，实际 {n1}")
        if pr1.get("skipped") != 0:
            fails.append(f"G4: 首次 skipped 应为0，实际 {pr1.get('skipped')}")

        # G5 bundle 内容
        bnd = client.get("/bundle", params={"name": "企业A"}).json()
        c = bnd.get("counts", {})
        if c.get("products") != 2:
            fails.append(f"G5: products 应为2，实际 {c.get('products')}")
        if c.get("corpus") != 4:
            fails.append(f"G5: corpus 应为4，实际 {c.get('corpus')}")
        cbk = c.get("corpus_by_kind", {})
        if cbk.get("product_text") != 2 or cbk.get("article") != 1 or cbk.get("ingredient") != 1:
            fails.append(f"G5: corpus_by_kind 错误 {cbk}")
        if bnd.get("enterprise_id") != _ent_id(client):
            fails.append("G5: manifest.enterprise_id 不匹配")

        # G4：二次处理（哈希未变 → 跳过，计数 0）
        resp2 = client.post("/process", data={"name": "企业A"}).json()
        pr2 = _wait_process(client, resp2)
        if pr2.get("processed", 0) != 0:
            fails.append(f"G4: 二次处理应跳过全部（计数0），实际 {pr2.get('processed')}")
        if pr2.get("skipped") != 4:
            fails.append(f"G4: 二次 skipped 应为4，实际 {pr2.get('skipped')}")
    finally:
        repos._override_base = ""
        shutil.rmtree(tmp, ignore_errors=True)

    for name in ("G1", "G2", "G3", "G4", "G5"):
        status = "FAIL" if any(name in f for f in fails) else "PASS"
        print(f"[{status}] {name}")
    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL GREEN (GUI backend P5)")


def _ent_id(client):
    for x in client.get("/repos").json()["repos"]:
        if x["name"] == "企业A":
            return x["enterprise_id"]


def _wait_process(client, resp: dict, timeout: int = 60) -> dict:
    """等待异步处理完成，返回最终状态。

    resp 是 /process 的初始返回：
      - {"status": "started", ...} → 轮询 /process/status 直到 done/error
      - {"status": "done", ...} → 直接返回
      - 其他（兼容旧格式）→ 直接返回 resp
    """
    import time
    if resp.get("status") == "started":
        deadline = time.time() + timeout
        while time.time() < deadline:
            s = client.get("/process/status").json()
            if s.get("status") != "running":
                return s
            time.sleep(0.5)
        return {"status": "timeout", "error": "处理超时"}
    # done 或旧格式
    if "processed" not in resp and "processed_files" in resp:
        resp["processed"] = len(resp["processed_files"])
    return resp


if __name__ == "__main__":
    main()
