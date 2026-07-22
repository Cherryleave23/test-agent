"""处理编排：复用 dataproc 引擎 build_bundle，并写处理标记（防重复）。

支持：
- force=True 时忽略已处理标记，强制重新处理
- out_dir 自定义输出目录（默认仓库 .dataproc/bundle）
- GUI 设置中的 ocr_enabled/run_real_ocr 通过环境变量注入到 dataproc 引擎
- 后台线程处理 + progress_cb 进度回调
"""
import json
import os
import threading
import time

from dataproc.build import build_bundle, expand_selection

from .repos import get_repo, _DEFAULT_BASE, SETTINGS_FILE
from .markers import is_processed, mark_processed, clear_markers
from . import progress


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


def _make_progress_cb():
    """创建进度回调函数，更新全局 progress 状态。"""
    def cb(event: str, rel: str, *args):
        if event == "processing":
            progress.set_current(rel)
            progress.add_log(f"开始处理: {rel}")
        elif event == "done":
            progress.add_processed()
            progress.add_log(f"✓ 完成: {rel}")
        elif event == "error":
            err_msg = args[0] if args else "未知错误"
            progress.add_processed()
            progress.add_log(f"✗ 失败: {rel} — {err_msg}")
    return cb


def process(name: str, selection: dict = None, base: str = None,
            force: bool = False, out_dir: str = None) -> dict:
    """同步处理（兼容旧接口，测试用）。"""
    repo_dir, meta = get_repo(name, base)
    _apply_ocr_env()
    s = _load_gui_settings()
    actual_out = out_dir or meta.get("output_dir") or s.get("output_dir") or os.path.join(repo_dir, ".dataproc", "bundle")

    all_files = expand_selection(repo_dir, selection)
    if force:
        clear_markers(name, base)
        to_process = list(all_files)
    else:
        to_process = [f for f in all_files if not is_processed(name, f, base)]

    if not to_process:
        manifest_path = os.path.join(actual_out, "manifest.json")
        if not os.path.isfile(manifest_path) and all_files:
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


def process_async(name: str, selection: dict = None, base: str = None,
                  force: bool = False, out_dir: str = None) -> dict:
    """异步处理：在后台线程中运行，立即返回。

    前端通过 GET /process/status 轮询进度。
    """
    # 如果已有任务在运行，拒绝重复
    cur = progress.get()
    if cur["status"] == "running":
        return {"error": "已有处理任务在运行", "status": cur}

    repo_dir, meta = get_repo(name, base)
    _apply_ocr_env()
    s = _load_gui_settings()
    actual_out = out_dir or meta.get("output_dir") or s.get("output_dir") or os.path.join(repo_dir, ".dataproc", "bundle")

    all_files = expand_selection(repo_dir, selection)
    if force:
        clear_markers(name, base)
        to_process = list(all_files)
    else:
        to_process = [f for f in all_files if not is_processed(name, f, base)]

    if not to_process:
        manifest_path = os.path.join(actual_out, "manifest.json")
        if not os.path.isfile(manifest_path) and all_files:
            to_process = list(all_files)
            clear_markers(name, base)

    if not to_process:
        return {"out_dir": actual_out, "manifest": None,
                "processed_files": [], "skipped": len(all_files),
                "status": "done"}

    skipped = len(all_files) - len(to_process)
    total = len(to_process)
    progress.start(total=total, skipped=skipped)
    progress.add_log(f"开始处理 {total} 个文件（跳过 {skipped} 个已处理）")

    if s.get("run_real_ocr"):
        progress.add_log("⚠ 真实 OCR 已开启，图片处理可能需要数分钟/张")

    def _worker():
        try:
            cb = _make_progress_cb()
            summary = build_bundle(
                repo_dir, actual_out,
                selection={"files": to_process},
                progress_cb=cb,
            )
            for rel in summary["processed_files"]:
                mark_processed(name, rel, "processed", "bundle", base)
            progress.add_log(
                f"处理完成: {len(summary['processed_files'])} 个文件, "
                f"跳过 {skipped} 个"
            )
            progress.finish()
        except Exception as e:
            progress.add_log(f"✗ 处理失败: {type(e).__name__}: {e}")
            progress.finish(error=str(e))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    return {"status": "started", "total": total, "skipped": skipped}
