"""请求体模型。"""
from typing import Optional

from pydantic import BaseModel


class RepoCreate(BaseModel):
    name: str
    namespace: str = "b"  # b=企业自有；hq=总部共享库
    path: Optional[str] = None  # 自定义磁盘路径（None=使用默认 REPOS_BASE）
    output_dir: Optional[str] = None  # 每仓库独立输出目录（None=使用仓库内 .dataproc/bundle）


class ProcessRequest(BaseModel):
    selection: Optional[dict] = None  # None=全量；{"files":[...]} / {"folders":[...]}
    force: bool = False  # 强制重新处理（忽略已处理标记）
    out_dir: Optional[str] = None  # 自定义输出目录


class SettingsUpdate(BaseModel):
    ocr_enabled: Optional[bool] = None
    run_real_ocr: Optional[bool] = None
    output_dir: Optional[str] = None
    repos_base: Optional[str] = None  # 仓库根目录
