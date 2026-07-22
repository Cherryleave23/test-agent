"""bundle 加载器（F4）：把 tools/dataproc 产出的 NDJSON bundle 灌入 KnowledgeStore。

- `load_bundle(bundle_dir, store, enterprise_id)`：读 manifest.json 校验后，按
  products.ndjson → hq_products.ndjson → corpus.ndjson 顺序加载；先建结构化产品拿到
  pid，再让 product_text 语料按 product_uid→product_id 绑定（F1 契约）。单条失败不中断整包、
  记入 errors（不静默丢弃、不谎称成功）。
- `scan_and_load(inbox_dir, ...)`：扫描收件箱子目录（各含 manifest.json），逐个 load_bundle；
  成功移入 processed/，失败移入 failed/ 并留痕。移动即幂等：已处理包不会再被加载。
- `load_on_startup(store, enterprise_id, inbox_dir=None)`：agent 启动钩子（读 BUNDLE_INBOX_DIR
  环境变量）；未配置/目录缺失则为 no-op，可安全接入 build_instance 不影响既有测试。

触发机制（PRD §九 F4）：vendor/企业 IT 把 bundle 拷入收件箱 → agent 启动自动扫目录加载，
或（后续）微信管理指令调 scan_and_load 手动触发。
"""
from __future__ import annotations

import json
import os
import shutil
from typing import Optional

from kb.store import KnowledgeStore, HQ_ENT
from kb.models import MilkProduct, NutritionProduct


# ---------- 小工具 ----------
def _read_lines(path: str):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _safe_move(src: str, dest: str) -> None:
    """移动目录；目标已存在先清，避免 shutil.move 对目录冲突报错。"""
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.move(src, dest)


def _as_product(cls, fields: dict, enterprise_id: str):
    """用 bundle 的 fields 构造产品模型；缺字段按类型补默认，避免构造抛错。"""
    kw: dict = {}
    for k, f in cls.__dataclass_fields__.items():
        if k == "id":
            continue
        if k == "enterprise_id":
            kw[k] = enterprise_id
            continue
        v = fields.get(k)
        if v is None:
            v = 0.0 if f.type == "float" else ""
        kw[k] = v
    return cls(**kw)


def _load_product(store: KnowledgeStore, enterprise_id: str, rec: dict) -> Optional[int]:
    kind = rec.get("kind", "milk")
    fields = dict(rec.get("fields", {}))
    if kind == "nutrition":
        pid = store.add_nutrition(_as_product(NutritionProduct, fields, enterprise_id))
    else:
        pid = store.add_milk(_as_product(MilkProduct, fields, enterprise_id))
    return pid


def _load_corpus(store: KnowledgeStore, enterprise_id: str, rec: dict,
                 uid_to_pid: dict) -> None:
    part = rec.get("part", "b_kb")
    title = rec.get("title", "")
    content = rec.get("content", "")
    meta = dict(rec.get("meta", {}))
    product_uid = rec.get("product_uid")
    product_id = uid_to_pid.get(product_uid) if product_uid else None
    if part == "hq_kb":
        store.add_hq_knowledge(title, content, meta)
    else:
        store.add_knowledge(enterprise_id, title, content, meta, product_id=product_id)


# ---------- 核心：加载单个 bundle ----------
def load_bundle(bundle_dir, store: KnowledgeStore, enterprise_id: str) -> dict:
    """把一个 dataproc bundle 目录加载进 store。返回统计与逐条错误。"""
    bundle_dir = str(bundle_dir)
    manifest_path = os.path.join(bundle_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        raise FileNotFoundError(f"bundle 缺少 manifest.json: {bundle_dir}")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    bundle_ent = manifest.get("enterprise_id")
    if bundle_ent not in (enterprise_id, HQ_ENT):
        raise ValueError(
            f"bundle enterprise_id={bundle_ent!r} 与本实例 {enterprise_id!r} 不匹配"
            f"（仅允许本企业或 HQ 共享库 hq）"
        )

    stats = {"products": 0, "hq_products": 0, "corpus": 0, "errors": []}
    uid_to_pid: dict = {}

    # 1) 结构化产品（先建，拿到 pid 供语料绑定）
    products_path = os.path.join(bundle_dir, "products.ndjson")
    if os.path.isfile(products_path):
        for line in _read_lines(products_path):
            try:
                pid = _load_product(store, enterprise_id, line)
                if pid is not None and line.get("uid"):
                    uid_to_pid[line["uid"]] = pid
                stats["products"] += 1
            except Exception as e:  # noqa: BLE001
                stats["errors"].append(
                    f"product {line.get('source_ref', line.get('uid', '?'))}: {e}")

    # 2) HQ 商品库种子
    hq_path = os.path.join(bundle_dir, "hq_products.ndjson")
    if os.path.isfile(hq_path):
        for line in _read_lines(hq_path):
            try:
                store.add_hq_product(line.get("fields", {}), line.get("meta", {}))
                stats["hq_products"] += 1
            except Exception as e:  # noqa: BLE001
                stats["errors"].append(
                    f"hq_product {line.get('fields', {}).get('name', '?')}: {e}")

    # 3) RAG 语料（product_text 按 uid→pid 绑定）
    corpus_path = os.path.join(bundle_dir, "corpus.ndjson")
    if os.path.isfile(corpus_path):
        for line in _read_lines(corpus_path):
            try:
                _load_corpus(store, enterprise_id, line, uid_to_pid)
                stats["corpus"] += 1
            except Exception as e:  # noqa: BLE001
                stats["errors"].append(f"corpus {line.get('title', '?')}: {e}")

    return stats


# ---------- 触发：收件箱扫描 + 启动钩子 ----------
def scan_and_load(inbox_dir, store: KnowledgeStore, enterprise_id: str,
                  processed_dir: Optional[str] = None,
                  failed_dir: Optional[str] = None) -> dict:
    """扫描收件箱：逐个加载合法 bundle，成功→processed/，失败→failed/。"""
    inbox_dir = str(inbox_dir)
    if not os.path.isdir(inbox_dir):
        return {"loaded": [], "failed": [], "errors": [], "note": "no inbox dir"}
    processed_dir = processed_dir or os.path.join(inbox_dir, "processed")
    failed_dir = failed_dir or os.path.join(inbox_dir, "failed")
    os.makedirs(processed_dir, exist_ok=True)
    os.makedirs(failed_dir, exist_ok=True)

    result = {"loaded": [], "failed": [], "errors": []}
    for name in sorted(os.listdir(inbox_dir)):
        src = os.path.join(inbox_dir, name)
        if not os.path.isdir(src) or name in ("processed", "failed"):
            continue
        if not os.path.isfile(os.path.join(src, "manifest.json")):
            continue
        try:
            stats = load_bundle(src, store, enterprise_id)
            _safe_move(src, os.path.join(processed_dir, name))
            result["loaded"].append({"bundle": name, "stats": stats})
        except Exception as e:  # noqa: BLE001
            _safe_move(src, os.path.join(failed_dir, name))
            result["failed"].append({"bundle": name, "error": repr(e)})
            result["errors"].append(f"{name}: {e}")
    return result


def load_on_startup(store: KnowledgeStore, enterprise_id: str,
                    inbox_dir: Optional[str] = None) -> dict:
    """agent 启动钩子：扫 BUNDLE_INBOX_DIR（或显式传入）自动加载 bundle。"""
    inbox_dir = inbox_dir or os.environ.get("BUNDLE_INBOX_DIR")
    if not inbox_dir or not os.path.isdir(inbox_dir):
        return {"scanned": False, "reason": "no BUNDLE_INBOX_DIR or dir missing"}
    return scan_and_load(inbox_dir, store, enterprise_id)
