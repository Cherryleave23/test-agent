"""处理进度跟踪器：全局状态 + 线程安全。

前端通过 GET /process/status 轮询此状态。
"""
import threading
import time

_lock = threading.Lock()

_progress = {
    "status": "idle",       # idle | running | done | error
    "total": 0,
    "processed": 0,
    "skipped": 0,
    "current_file": "",
    "logs": [],
    "error": "",
    "started_at": 0,
    "finished_at": 0,
}


def reset():
    with _lock:
        _progress.update({
            "status": "idle",
            "total": 0,
            "processed": 0,
            "skipped": 0,
            "current_file": "",
            "logs": [],
            "error": "",
            "started_at": 0,
            "finished_at": 0,
        })


def start(total: int, skipped: int = 0):
    with _lock:
        _progress.update({
            "status": "running",
            "total": total,
            "processed": 0,
            "skipped": skipped,
            "current_file": "",
            "logs": [],
            "error": "",
            "started_at": time.time(),
            "finished_at": 0,
        })


def set_current(file: str):
    with _lock:
        _progress["current_file"] = file


def add_processed():
    with _lock:
        _progress["processed"] += 1


def add_log(msg: str):
    with _lock:
        _progress["logs"].append(msg)
        if len(_progress["logs"]) > 100:
            _progress["logs"] = _progress["logs"][-100:]


def finish(error: str = ""):
    with _lock:
        _progress["status"] = "error" if error else "done"
        _progress["error"] = error
        _progress["finished_at"] = time.time()
        _progress["current_file"] = ""


def get() -> dict:
    with _lock:
        d = dict(_progress)
        if d["started_at"] and not d["finished_at"]:
            d["elapsed"] = round(time.time() - d["started_at"], 1)
        elif d["started_at"] and d["finished_at"]:
            d["elapsed"] = round(d["finished_at"] - d["started_at"], 1)
        else:
            d["elapsed"] = 0
        return d
