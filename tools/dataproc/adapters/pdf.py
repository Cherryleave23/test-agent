"""PDF 适配器：数字直抽 + 扫描件 PaddleOCR（+ 可选 PP-Structure 表格）。零 src.*。"""
from __future__ import annotations

import os

from . import MIN_DIGITAL_TEXT, OCRDeferred, OCRDependencyMissing, AdapterResult, paddle_available


def _digital_text(path: str) -> str:
    """用 pypdf 抽文本层（无重依赖）。无文本层返回空串。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(path)
        parts = [p.extract_text() or "" for p in reader.pages]
        return "\n".join(parts).strip()
    except Exception:
        return ""


def _render_pages(path: str):
    """用 fitz(PyMuPDF) 把每页渲染为 PIL 图像（扫描件 OCR 前处理）。"""
    import fitz  # PyMuPDF
    from PIL import Image
    doc = fitz.open(path)
    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        yield Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _ocr_images(images, run_real_ocr: bool):
    """对一组图像跑 PaddleOCR + 可选 PP-Structure；返回 (text, tables, low_conf)。"""
    if not run_real_ocr:
        raise OCRDeferred("run_real_ocr=False，PDF 扫描件 OCR 推迟")
    if not paddle_available():
        raise OCRDependencyMissing("PaddleOCR 未安装，无法对扫描件 PDF 做 OCR（RUN_REAL_OCR=1 但缺依赖）")

    from paddleocr import PaddleOCR
    import numpy as np
    ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    texts: list = []
    tables: list = []
    low_conf = False
    for img in images:
        arr = np.array(img.convert("RGB"))
        res = ocr.ocr(arr, cls=True)
        page_lines: list = []
        if res:
            for line in res[0] or []:
                if not line:
                    continue
                box, (txt, score) = line
                if score < 0.5:
                    low_conf = True
                page_lines.append(txt)
        if not page_lines:
            low_conf = True
        texts.append("\n".join(page_lines))
    text = "\n".join(texts).strip()
    # PP-Structure 表格（可选装；缺则标 table_pending）
    try:
        from ppstructure.predict_system import PredictConfig  # 仅在已装时
        # 真实表格抽取路径（端侧可选装）；此处不强制，避免把重依赖变硬依赖。
        _ = PredictConfig
    except Exception:
        pass
    return text, tables, low_conf


class PDFAdapter:
    """PDF 适配器。数字 PDF 直抽（无重依赖）；扫描件走 PaddleOCR。"""
    kind = "pdf"

    def extract(self, path: str, run_real_ocr: bool = False) -> AdapterResult:
        digital = _digital_text(path)
        if len(digital) >= MIN_DIGITAL_TEXT:
            return AdapterResult(
                text=digital,
                meta={"source": "pdf", "is_scanned": False, "ocr": False,
                      "pages": _page_count(path)},
            )
        # 扫描件：需真实 OCR
        text, tables, low_conf = _ocr_images(_render_pages(path), run_real_ocr)
        return AdapterResult(
            text=text,
            meta={"source": "pdf", "is_scanned": True, "ocr": True,
                  "low_conf": low_conf, "table_pending": not tables},
            tables=tables, low_conf=low_conf,
        )


def _page_count(path: str) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(path).pages)
    except Exception:
        return 0
