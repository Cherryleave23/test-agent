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
import logging
import os
import re
from datetime import datetime, timezone
from typing import List, Optional

from .repo import load_meta, TOP_FOLDERS, KIND_BY_TOP
from .schema import ProductRecord, CorpusRecord, HQProductRecord
from .config import load_config
from .adapters import get_adapter, OCR_EXTS, IMAGE_EXTS, OCRDeferred, OCRDependencyMissing
from .structurer import structure, resolve
from .classifier import classify
from .llms import from_config

TOOL_VERSION = "dataproc 0.1.0"
SCHEMA_VERSION = "1.0"

logger = logging.getLogger(__name__)


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


def _all_repo_files(repo_dir: str) -> List[str]:
    """仓库内全部文件（相对路径），跳过内部目录 .dataproc。"""
    out: List[str] = []
    for root, _dirs, files in os.walk(repo_dir):
        if ".dataproc" in root.split(os.sep):
            continue
        for fn in files:
            full = os.path.join(root, fn)
            out.append(os.path.relpath(full, repo_dir).replace(os.sep, "/"))
    return sorted(out)


def _unmatched_files(repo_dir: str) -> List[str]:
    """不在任何标准总文件夹下的文件（真人拖错位置/命名不符），build 会静默忽略。

    返回这些文件供调用方给出可见反馈（避免「资料凭空丢失」而无任何提示）。
    """
    prefixes = tuple(t + "/" for t in TOP_FOLDERS)
    out = []
    for rel in _all_repo_files(repo_dir):
        if not rel.startswith(prefixes):
            out.append(rel)
    return out


# ---------- 内容解析（不 import src.*） ----------
def _parse_md_product(path: str):
    with open(path, encoding="utf-8", errors="ignore") as f:
        text = f.read()
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


def _load_known(catalog_path: str):
    """加载已知商品目录（实体解析已知列表）。失败/缺失返回 None。"""
    if not catalog_path or not os.path.isfile(catalog_path):
        return None
    out: List[dict] = []
    try:
        with open(catalog_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except Exception:
        return None
    return out or None


def _process_nontext(repo_dir, rel, full_path, kind, cfg, provider, known, state):
    """处理非 md/txt 文件：尝试 OCR（若开启），可选结构化抽取（product_text）。

    返回 (content, meta, optional product_dict, optional product_uid)。
    - .pdf：数字文本层直抽始终尝试（轻量、无重依赖，I7 默认绿）；仅扫描件 OCR 需
      ocr_enabled + run_real_ocr + PaddleOCR。
    - 图片/规格表：需 ocr_enabled + run_real_ocr + PaddleOCR，否则保持 ocr_pending（I13）。
    - 缺依赖/适配器抛错：记录 ocr_error、保持占位，不崩全局（I5/I11）。
    """
    ext = os.path.splitext(rel)[1].lower()

    def _placeholder(meta_extra=None):
        m = {"source": (ext.lstrip(".") or "file"), "path": rel, "ocr_pending": True}
        if meta_extra:
            m.update(meta_extra)
        # A-反馈：OCR 未启用/缺依赖导致内容被延迟（占位为空），必须给操作者可见警告，
        # 否则该文件看似「已处理」实则无内容进入语料，造成静默数据损失。
        logger.warning(
            "OCR 未启用或依赖缺失，文件内容延迟处理（已写入空占位 ocr_pending，"
            "非真实内容）：%s", rel)
        return "", m, None, None

    if ext == ".pdf":
        try:
            res = get_adapter(ext).extract(full_path, cfg.run_real_ocr)
        except OCRDeferred:
            return _placeholder()
        except Exception as e:
            return _placeholder({"ocr_error": str(e)[:200]})
    elif ext in IMAGE_EXTS:
        if not (cfg.ocr_enabled and cfg.run_real_ocr):
            return _placeholder()
        try:
            res = get_adapter(ext).extract(full_path, cfg.run_real_ocr)
        except OCRDeferred:
            return _placeholder()
        except Exception as e:
            return _placeholder({"ocr_error": str(e)[:200]})
    else:
        # md/txt：直接读正文（不标 ocr_pending）；其余未知扩展名：占位
        if ext in (".md", ".txt"):
            with open(full_path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            return content, {"source": ext.lstrip("."), "path": rel}, None, None
        return _placeholder()

    content = res.text or ""
    meta = {"source": (ext.lstrip(".") or "file"), "path": rel, "ocr": res.meta.get("ocr", False)}
    for k, v in res.meta.items():
        if k != "ocr":
            meta[k] = v
    product_dict = None
    product_uid = None
    if not content:
        meta["ocr_pending"] = True
    if content and kind == "product_text":
        st = structure(content, provider)
        # P3 分类推断：ptype + product_category（规则 + conf.yaml 覆盖）
        cls = classify(content)
        if cls["ptype"]:
            st.fields.setdefault("ptype", cls["ptype"])
        if cls["product_category"]:
            st.fields.setdefault("product_category", cls["product_category"])
        if st.fields:
            r = resolve(st.fields, known)
            product_uid = r["uid"]
            # 范式②：规则与 LLM 在权威/描述字段上冲突 → 标 needs_review（不静默采用任一方）
            prod_status = "needs_review" if st.needs_review else r["status"]
            prod_kind = "nutrition" if cls.get("product_category") == "营养品" else "milk"
            product_dict = ProductRecord(
                kind=prod_kind, uid=r["uid"], status=prod_status, source_ref=rel,
                resolved=r["resolved"], fields=st.fields,
            ).to_dict()
            if st.provider_used not in ("rule-only", "rule-only(fallback)"):
                state["structuring_provider"] = f"{st.provider_used}://{cfg.llm.model}"
            else:
                state["structuring_provider"] = "rule-only"
    return content, meta, product_dict, product_uid


# ---------- 核心：构建 bundle ----------
def build_bundle(repo_dir: str, out_dir: str, selection: Optional[dict] = None,
                  progress_cb: Optional[callable] = None) -> dict:
    meta = load_meta(repo_dir)
    namespace = meta.get("namespace", "b")
    ent_id = meta.get("enterprise_id", "ent_unknown")
    part = "hq_kb" if namespace == "hq" else "b_kb"

    cfg = load_config()
    provider = from_config(cfg.llm)
    known = _load_known(cfg.known_catalog)
    state = {"structuring_provider": "none (Tier, 待配置)"}

    products: List[dict] = []
    corpus: List[dict] = []
    hq_products: List[dict] = []

    # 展开选择：只调用一次 expand_selection，复用结果作为 selected 与 processed_files
    if selection is not None:
        processed_files = expand_selection(repo_dir, selection)
        selected = set(processed_files)
        full = False
    else:
        processed_files = [r for top in TOP_FOLDERS for r in _walk_top(repo_dir, top)]
        selected = None
        full = True

    logger.info("build_bundle: %d files to process (full=%s)", len(processed_files), full)

    for top in TOP_FOLDERS:
        kind = KIND_BY_TOP[top]
        for rel in _walk_top(repo_dir, top):
            if not full and rel not in selected:
                continue
            full_path = os.path.join(repo_dir, rel)
            ext = os.path.splitext(rel)[1].lower()

            if progress_cb:
                progress_cb("processing", rel)

            try:
                if kind == "product_text" and ext == ".md":
                    fields, body = _parse_md_product(full_path)
                    reg = fields.get("reg_number")
                    uid = ("reg:" + reg) if reg else _detect_product_uid(repo_dir, rel)
                    status = "confirmed" if reg else "pending"
                    cls = classify(body)
                    prod_kind = "nutrition" if cls.get("product_category") == "营养品" else "milk"
                    products.append(ProductRecord(
                        kind=prod_kind, uid=uid, status=status, source_ref=rel,
                        resolved={"match": "reg_number" if reg else "tuple",
                                   "key": ["brand", "name", "stage"]},
                        fields=fields,
                    ).to_dict())
                    if namespace == "hq":
                        hq_products.append(HQProductRecord(
                            kind=prod_kind, fields=fields, meta={"vendor": ent_id}).to_dict())
                    corpus.append(CorpusRecord(
                        part=part, kind="product_text", title=os.path.basename(rel),
                        content=body, product_uid=uid,
                        meta={"source": "md", "path": rel}, lang="zh").to_dict())
                else:
                    content, meta, product_dict, product_uid = _process_nontext(
                        repo_dir, rel, full_path, kind, cfg, provider, known, state)
                    if product_dict:
                        products.append(product_dict)
                        if namespace == "hq":
                            # P2-N6: 从 product_dict 推断 kind，不再硬编码 "milk"
                            hq_kind = product_dict.get("kind", "milk")
                            hq_products.append(HQProductRecord(
                                kind=hq_kind, fields=product_dict["fields"],
                                meta={"vendor": ent_id}).to_dict())
                    corpus.append(CorpusRecord(
                        part=part, kind=kind, title=os.path.basename(rel), content=content,
                        product_uid=product_uid, meta=meta, lang="zh").to_dict())
            except Exception as e:
                logger.error("处理文件失败 %s: %s: %s", rel, type(e).__name__, e)
                if progress_cb:
                    progress_cb("error", rel, str(e))
                continue

            if progress_cb:
                progress_cb("done", rel)

    # 反馈：标准总文件夹之外的文件（真人拖错位置/文件夹命名不符）会被静默忽略，
    # 此处显式告警并记入 manifest.skipped_files，避免「资料凭空丢失」却无任何提示。
    unmatched = _unmatched_files(repo_dir)
    for rel in unmatched:
        logger.warning("文件不在标准总文件夹（%s）下，已被忽略、未进入 bundle：%s",
                       "/".join(TOP_FOLDERS), rel)

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
            "ocr_pending": sum(1 for c in corpus if c.get("meta", {}).get("ocr_pending")),
            "needs_review": sum(1 for p in products if p.get("status") == "needs_review"),
            "skipped_files": len(unmatched),
        },
        "checksums": {
            n: _sha_file(os.path.join(out_dir, n))
            for n in ("products.ndjson", "corpus.ndjson", "hq_products.ndjson")
        },
        "skipped_files": unmatched,
        "structuring_provider": state["structuring_provider"],
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return {"out_dir": out_dir, "manifest": manifest, "processed_files": processed_files}
