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
    """对一组图像跑 PaddleOCR + PP-Structure 表格识别；返回 (text, tables, low_conf)。"""
    if not run_real_ocr:
        raise OCRDeferred("run_real_ocr=False，PDF 扫描件 OCR 推迟")
    if not paddle_available():
        raise OCRDependencyMissing("PaddleOCR 未安装，无法对扫描件 PDF 做 OCR（RUN_REAL_OCR=1 但缺依赖）")

    from paddleocr import PaddleOCR
    import numpy as np
    ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    # PP-Structure 表格识别引擎（与 PaddleOCR 同包，但独立模型）
    pp_engine = _try_init_ppstructure()
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
        # PP-Structure 表格抽取
        if pp_engine is not None:
            page_tables = _extract_tables_ppstructure(pp_engine, arr)
            tables.extend(page_tables)
    text = "\n".join(texts).strip()
    return text, tables, low_conf


def _try_init_ppstructure():
    """尝试初始化 PP-Structure 引擎；缺依赖返回 None（调用方保持 table_pending）。"""
    try:
        from paddleocr import PPStructure
        return PPStructure(show_log=False, layout=True, table=True,
                           ocr=True, structure_version="PP-StructureV2")
    except Exception:
        return None


def _extract_tables_ppstructure(pp_engine, img_array) -> list:
    """用 PP-Structure 从图像中抽取表格，返回 table dict 列表。

    每个 table dict 包含：
    - html: 表格 HTML 结构字符串
    - cells: [[row_val, ...], ...] 二维数组（从 HTML 解析）
    - bbox: [x1, y1, x2, y2] 表格区域坐标
    """
    import re
    from html.parser import HTMLParser

    class _TableHTMLParser(HTMLParser):
        """从 PP-Structure 输出的 HTML 中解析单元格为二维数组。"""

        def __init__(self):
            super().__init__()
            self.rows: list = []
            self._cur_row: list = []
            self._cur_cell: str = ""
            self._in_cell = False

        def handle_starttag(self, tag, attrs):
            if tag == "tr":
                self._cur_row = []
            elif tag in ("td", "th"):
                self._in_cell = True
                self._cur_cell = ""

        def handle_endtag(self, tag):
            if tag == "tr":
                if self._cur_row:
                    self.rows.append(self._cur_row)
            elif tag in ("td", "th"):
                self._cur_row.append(self._cur_cell.strip())
                self._in_cell = False

        def handle_data(self, data):
            if self._in_cell:
                self._cur_cell += data

    out: list = []
    try:
        results = pp_engine(img_array)
        if not results:
            return out
        for region in results:
            if not isinstance(region, dict):
                continue
            if region.get("type") != "table":
                continue
            res = region.get("res", {})
            html = res.get("html", "") if isinstance(res, dict) else ""
            bbox = region.get("bbox", [0, 0, 0, 0])
            if not html:
                continue
            # 从 HTML 解析单元格
            parser = _TableHTMLParser()
            parser.feed(html)
            cells = parser.rows if parser.rows else []
            out.append({
                "html": html,
                "cells": cells,
                "bbox": bbox,
            })
    except Exception:
        pass  # PP-Structure 表格抽取失败不阻断 OCR 文本
    return out


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
