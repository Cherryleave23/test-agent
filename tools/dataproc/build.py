"""dataproc build —— 把仓库资料归一为 NDJSON bundle（产物契约）。

设计原则（与 PRD 一致）：
- 引擎只做编排 + 诚实搬运；OCR/结构化抽取/LLM 为 Tier（可选装）。
- 非 md/txt 文件（图片/PDF）：先记录溯源占位、标 `ocr_pending`，**不编造**内容，
  待 OCR Tier 阶段回填。
- 固定三类总文件夹决定 corpus `kind`；产品资料下的嵌套路径决定 `product_uid` 层级键。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import List, Optional

from .repo import load_meta, TOP_FOLDERS, KIND_BY_TOP
from .schema import ProductRecord, CorpusRecord, HQProductRecord

TOOL_VERSION = "dataproc 0.1.0"
SCHEMA_VERSION = "1.0"


# ---------- 小工具 ----------
def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha1(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8"))
    return h.hexdigest()


def _sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_by_kind(corpus: List[dict]) -> dict:
    out: dict = {}
    for c in corpus:
        out[c["kind"]] = out.get(c["kind"], 0) + 1
    return out


# ---------- 文件遍历 / 选择展开 ----------
def _walk_top(repo_dir: str, top: str):
    base = os.path.join(repo_dir, top)
    if not os.path.isdir(base):
        return
    for root, _dirs, files in os.walk(base):
        for fn in sorted(files):
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, repo_dir).replace(os.sep, "/")
            yield rel


def _all_under(repo_dir: str, folder_rel: str) -> List[str]:
    base = os.path.join(repo_dir, folder_rel)
    out: List[str] = []
    if not os.path.isdir(base):
        return out
    for root, _dirs, files in os.walk(base):
        for fn in files:
            full = os.path.join(root, fn)
            out.append(os.path.relpath(full, repo_dir).replace(os.sep, "/"))
    return sorted(out)


def expand_selection(repo_dir: str, selection: Optional[dict]) -> List[str]:
    """展开 selection 为相对路径文件列表。selection=None → 全量。"""
    if selection is None:
        out: List[str] = []
        for top in TOP_FOLDERS:
            out.extend(_walk_top(repo_dir, top))
        return out
    out = list(selection.get("files", []))
    for fol in selection.get("folders", []):
        out.extend(_all_under(repo_dir, fol))
    return out


# ---------- 内容解析（不 import src.*） ----------
def _parse_md_product(path: str):
    text = open(path, encoding="utf-8", errors="ignore").read()
    fields: dict = {}
    body = text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fields[k.strip()] = v.strip()
        body = m.group(2)
    return fields, body


def _detect_product_uid(repo_dir: str, rel_path: str) -> str:
    parts = rel_path.split("/")
    sub = "/".join(parts[1:-1])  # 去掉总文件夹与文件名
    if not sub:
        sub = parts[-1]
    return "tuple:" + _sha1(sub)


# ---------- 核心：构建 bundle ----------
def build_bundle(repo_dir: str, out_dir: str, selection: Optional[dict] = None) -> dict:
    meta = load_meta(repo_dir)
    namespace = meta.get("namespace", "b")
    ent_id = meta.get("enterprise_id", "ent_unknown")
    part = "hq_kb" if namespace == "hq" else "b_kb"

    products: List[dict] = []
    corpus: List[dict] = []
    hq_products: List[dict] = []

    selected = set(expand_selection(repo_dir, selection)) if selection is not None else None
    full = selection is None

    for top in TOP_FOLDERS:
        kind = KIND_BY_TOP[top]
        for rel in _walk_top(repo_dir, top):
            if not full and rel not in selected:
                continue
            full_path = os.path.join(repo_dir, rel)
            ext = os.path.splitext(rel)[1].lower()

            if kind == "product_text" and ext == ".md":
                fields, body = _parse_md_product(full_path)
                reg = fields.get("reg_number")
                uid = ("reg:" + reg) if reg else _detect_product_uid(repo_dir, rel)
                status = "confirmed" if reg else "pending"
                products.append(ProductRecord(
                    kind="milk", uid=uid, status=status, source_ref=rel,
                    resolved={"match": "reg_number" if reg else "tuple",
                               "key": ["brand", "name", "stage"]},
                    fields=fields,
                ).to_dict())
                if namespace == "hq":
                    hq_products.append(HQProductRecord(
                        kind="milk", fields=fields, meta={"vendor": ent_id}).to_dict())
                corpus.append(CorpusRecord(
                    part=part, kind="product_text", title=os.path.basename(rel),
                    content=body, product_uid=uid,
                    meta={"source": "md", "path": rel}, lang="zh").to_dict())
            else:
                content = ""
                if ext in (".md", ".txt"):
                    content = open(full_path, encoding="utf-8", errors="ignore").read()
                corpus.append(CorpusRecord(
                    part=part, kind=kind, title=os.path.basename(rel), content=content,
                    meta={"source": (ext.lstrip(".") or "file"), "path": rel,
                          "ocr_pending": ext not in (".md", ".txt")},
                    lang="zh").to_dict())

    os.makedirs(out_dir, exist_ok=True)

    def _write(name: str, rows: List[dict]) -> None:
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    _write("products.ndjson", products)
    _write("corpus.ndjson", corpus)
    _write("hq_products.ndjson", hq_products)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "enterprise_id": ent_id,
        "tool_version": TOOL_VERSION,
        "generated_at": _now(),
        "counts": {
            "products": len(products), "corpus": len(corpus),
            "hq_products": len(hq_products),
            "corpus_by_kind": _count_by_kind(corpus),
        },
        "checksums": {
            n: _sha_file(os.path.join(out_dir, n))
            for n in ("products.ndjson", "corpus.ndjson", "hq_products.ndjson")
        },
        "structuring_provider": "none (Tier, 待配置)",
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    processed_files = expand_selection(repo_dir, selection) if selection is not None else \
        [r for top in TOP_FOLDERS for r in _walk_top(repo_dir, top)]
    return {"out_dir": out_dir, "manifest": manifest, "processed_files": processed_files}
