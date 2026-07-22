"""dataproc OCR 适配器包（零 src.* 依赖）。

- `PDFAdapter`：数字 PDF 用 pypdf 直抽文本层（无需重依赖，I7 默认绿）；扫描件（无文本层）
  走 PaddleOCR，表格走 PP-Structure（可选装）。
- `ImageTableAdapter`：产品图/规格表/电商长图 → opencv 预处理 + PaddleOCR + 阅读顺序 +
  PP-Structure + 长图纵向切片。
- OCR 为 Tier1 重依赖（paddlepaddle/paddleocr），端侧可选装；`ocr_available()` 探测，
  缺依赖且真的要 OCR 时显式抛 `OCRDependencyMissing`（I11），不静默、不编造。
- `run_real_ocr=False`（默认）走 `OCRDeferred` → 调用方保持 ocr_pending 占位（I13）。
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from typing import List, Optional

# 支持 OCR 的扩展名
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
PDF_EXTS = {".pdf"}
OCR_EXTS = IMAGE_EXTS | PDF_EXTS

# 数字 PDF 直抽判定阈值（字符数）：低于则视为扫描件，需真 OCR
MIN_DIGITAL_TEXT = 30


class OCRDependencyMissing(Exception):
    """PaddleOCR 未安装，但请求了真实 OCR。"""


class OCRDeferred(Exception):
    """run_real_ocr=False，OCR 被推迟；调用方应保持 ocr_pending 占位。"""


@dataclass
class AdapterResult:
    text: str
    meta: dict = field(default_factory=dict)
    tables: List[dict] = field(default_factory=list)
    low_conf: bool = False


def paddle_available() -> bool:
    """探测 PaddleOCR 重依赖是否可用。"""
    return (importlib.util.find_spec("paddleocr") is not None
            and importlib.util.find_spec("paddle") is not None)


def get_adapter(ext: str) -> "object":
    """按扩展名返回适配器实例（不为 PDF/图片抛 ValueError）。"""
    ext = (ext or "").lower()
    if ext in PDF_EXTS:
        from .pdf import PDFAdapter
        return PDFAdapter()
    if ext in IMAGE_EXTS:
        from .image_table import ImageTableAdapter
        return ImageTableAdapter()
    raise ValueError(f"不支持的 OCR 扩展名：{ext}")
