"""处理编排：复用 dataproc 引擎 build_bundle，并写处理标记（防重复）。

支持：
- force=True 时忽略已处理标记，强制重新处理
- out_dir 自定义输出目录（默认仓库 .dataproc/bundle）
- GUI 设置中的 ocr_enabled/run_real_ocr 通过环境变量注入到 dataproc 引擎
"""
import json
import os

from dataproc.build import build_bundle, expand_selection

from .repos import get_repo, _DEFAULT_BASE, SETTINGS_FILE
from .markers import is_processed, mark_processed, clear_markers


def _load_gui_settings() -> dict:
    """加载 GUI 设置（ocr_enabled/run_real_ocr/output_dir）。"""
    sp = os.path.join(_DEFAULT_BASE, SETTINGS_FILE)
    if os.path.isfile(sp):
        try:
            with open(sp, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _apply_ocr_env():
    """把 GUI 设置中的 OCR 配置注入到环境变量，供 dataproc.config.load_config() 读取。"""
    s = _load_gui_settings()
    if s.get("ocr_enabled"):
        os.environ["DATAPROC_OCR_ENABLED"] = "1"
    else:
        os.environ.pop("DATAPROC_OCR_ENABLED", None)
    if s.get("run_real_ocr"):
        os.environ["RUN_REAL_OCR"] = "1"
    else:
        os.environ.pop("RUN_REAL_OCR", None)


def process(name: str, selection: dict = None, base: str = None,
            force: bool = False, out_dir: str = None) -> dict:
    repo_dir, _meta = get_repo(name, base)

    # 注入 OCR 设置到环境变量
    _apply_ocr_env()

    # 读取自定义输出目录（优先级：参数 > settings > 默认）
    s = _load_gui_settings()
    actual_out = out_dir or s.get("output_dir") or os.path.join(repo_dir, ".dataproc", "bundle")

    # 展开所有文件
    all_files = expand_selection(repo_dir, selection)

    if force:
        clear_markers(name, base)
        to_process = list(all_files)
    else:
        to_process = [f for f in all_files if not is_processed(name, f, base)]

    if not to_process:
        # 即使全部跳过，如果 manifest 不存在也要生成（首次跳过的边界情况）
        manifest_path = os.path.join(actual_out, "manifest.json")
        if not os.path.isfile(manifest_path) and all_files:
            # 全部已标记但 manifest 丢失 → 重新处理
            to_process = list(all_files)
            clear_markers(name, base)

    if not to_process:
        return {"out_dir": actual_out, "manifest": None,
                "processed_files": [], "skipped": len(all_files)}

    summary = build_bundle(repo_dir, actual_out, selection={"files": to_process})
    for rel in summary["processed_files"]:
        mark_processed(name, rel, "processed", "bundle", base)

    summary["skipped"] = len(all_files) - len(to_process)
    summary["out_dir"] = actual_out
    return summary
