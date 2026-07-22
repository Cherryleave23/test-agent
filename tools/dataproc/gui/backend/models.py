"""请求体模型。"""
from typing import Optional

from pydantic import BaseModel


class RepoCreate(BaseModel):
    name: str
    namespace: str = "b"  # b=企业自有；hq=总部共享库


class ProcessRequest(BaseModel):
    selection: Optional[dict] = None  # None=全量；{"files":[...]} / {"folders":[...]}
