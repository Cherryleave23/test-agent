"""后端小工具。"""
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()
