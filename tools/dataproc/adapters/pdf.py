"""PDF 适配器：数字直抽 + 扫描件 PaddleOCR 3.x。零 src.*。"""
from __future__ import annotations

import logging
import os

from . import MIN_DIGITAL_TEXT, OCRDeferred, OCRDependencyMissing, AdapterResult, paddle_available
from ._paddle_ocr import get_paddle_ocr

logger = logging.getLogger(__name__)


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
    except Exception as e:
        logger.warning("pypdf 文本抽取失败 %s: %s", type(e).__name__, e)
        return ""


def _render_pages(path: str):
    """用 fitz(PyMuPDF) 把每页渲染为 PIL 图像（扫描件 OCR 前处理）。"""
    import fitz  # PyMuPDF
    from PIL import Image
    doc = fitz.open(path)
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            yield Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()


def _ocr_images(images, run_real_ocr: bool):
    """对一组图像跑 PaddleOCR 3.x predict()。

    3.x 结果格式：OCRResult (dict 子类)
      - rec_texts: list[str]
      - rec_scores: list[float]
      - dt_polys: list[ndarray]
    """
    if not run_real_ocr:
        raise OCRDeferred("run_real_ocr=False，PDF 扫描件 OCR 推迟")
    if not paddle_available():
        raise OCRDependencyMissing("PaddleOCR 未安装，无法对扫描件 PDF 做 OCR（RUN_REAL_OCR=1 但缺依赖）")

    import numpy as np
    ocr = get_paddle_ocr()
    if ocr is None:
        raise OCRDependencyMissing("PaddleOCR 未安装，无法对扫描件 PDF 做 OCR（RUN_REAL_OCR=1 但缺依赖）")
    texts: list = []
    low_conf = False
    for img in images:
        arr = np.array(img.convert("RGB"))
        # PaddleOCR 3.x: predict()
        try:
            results = list(ocr.predict(arr))
        except (TypeError, AttributeError):
            # 兼容 2.x: ocr(cls=True)
            try:
                results = ocr.ocr(arr, cls=True)
            except TypeError:
                results = ocr.ocr(arr)
        page_lines: list = []
        if results:
            lines = _extract_lines(results)
            for line in lines:
                try:
                    box, (txt, score) = line
                except (ValueError, TypeError):
                    logger.warning("跳过无法解析的 OCR 行: %r", line)
                    continue
                if score < 0.5:
                    low_conf = True
                page_lines.append(txt)
        if not page_lines:
            low_conf = True
        texts.append("\n".join(page_lines))
    text = "\n".join(texts).strip()
    return text, low_conf


def _extract_lines(res):
    """从 PaddleOCR 结果中提取行列表（兼容 2.x 和 3.x 格式）。

    3.x: res = [OCRResult(dict子类)], OCRResult 有 rec_texts/rec_scores/dt_polys
    2.x: res = [[[box, (txt, score)], ...]]
    """
    if not res:
        return []
    first = res[0]

    # 3.x 格式：OCRResult 是 dict 子类，有 rec_texts 键
    if isinstance(first, dict) and "rec_texts" in first:
        d = first
        texts = d.get("rec_texts", [])
        scores = d.get("rec_scores", [])
        polys = d.get("dt_polys", [])
        lines = []
        for i in range(len(texts)):
            txt = texts[i] if i < len(texts) else ""
            score = float(scores[i]) if i < len(scores) else 0.0
            box = polys[i] if i < len(polys) else [[0, 0], [0, 0], [0, 0], [0, 0]]
            lines.append((box, (txt, score)))
        return lines

    # 2.x 格式：res[0] 是列表
    if isinstance(first, list):
        return first[0] if first and isinstance(first[0], list) else first

    return []


class PDFAdapter:
    """PDF 适配器。数字 PDF 直抽（无重依赖）；扫描件走 PaddleOCR 3.x。"""
    kind = "pdf"

    def extract(self, path: str, run_real_ocr: bool = False) -> AdapterResult:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"PDF 文件不存在: {path}")
        digital = _digital_text(path)
        if len(digital) >= MIN_DIGITAL_TEXT:
            return AdapterResult(
                text=digital,
                meta={"source": "pdf", "is_scanned": False, "ocr": False,
                      "pages": _page_count(path)},
            )
        # 扫描件：需真实 OCR
        text, low_conf = _ocr_images(_render_pages(path), run_real_ocr)
        return AdapterResult(
            text=text,
            meta={"source": "pdf", "is_scanned": True, "ocr": True,
                  "low_conf": low_conf},
            low_conf=low_conf,
        )


def _page_count(path: str) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(path).pages)
    except Exception as e:
        logger.warning("pypdf 页数读取失败 %s: %s", type(e).__name__, e)
        return 0
