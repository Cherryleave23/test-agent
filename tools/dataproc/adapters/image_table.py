"""图片/规格表/电商长图适配器：opencv 预处理 + PaddleOCR + 阅读顺序 + 长图切片。
零 src.*。无文字/低置信标 low_conf，绝不编造。"""
from __future__ import annotations

import logging

import numpy as np

from . import OCRDeferred, OCRDependencyMissing, AdapterResult, paddle_available
from ._ppstructure import extract_tables as _extract_tables_ppstructure, get_ppstructure
from ._paddle_ocr import get_paddle_ocr

logger = logging.getLogger(__name__)

# 长图纵向切片阈值：高 > SLICE_RATIO*宽 视为长图
SLICE_RATIO = 3.0
SLICE_H = 1200          # 单切片像素高
SLICE_OVERLAP = 120     # 切片重叠，避免切断行


def _get_cv2():
    """延迟导入 cv2，避免模块加载时硬依赖。"""
    import cv2
    return cv2


def _preprocess(img: np.ndarray) -> np.ndarray:
    """轻量预处理：缩放（限长边）→ 灰度 → CLAHE 增强对比。"""
    cv2 = _get_cv2()
    h, w = img.shape[:2]
    long_side = max(h, w)
    if long_side > 1600:
        scale = 1600 / long_side
        img = np.asarray(cv2.resize(img, (int(w * scale), int(h * scale))))
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _slice_long(gray: np.ndarray):
    """长图（高>宽数倍）纵向切片，带重叠避免切断。"""
    h, w = gray.shape
    if h <= SLICE_RATIO * w:
        yield gray
        return
    step = SLICE_H - SLICE_OVERLAP
    last_y = 0
    for y in range(0, max(h - SLICE_H, 0) + 1, step):
        yield gray[y:y + SLICE_H]
        last_y = y
    # 末片收尾：仅当还存在未被覆盖的尾部（且不与上一片完全重复）时补一片
    if h - SLICE_H > last_y:
        yield gray[h - SLICE_H:h]


def _reading_order(lines):
    """按阅读顺序排序：(min_y, min_x)。"""
    def key(ln):
        box = ln[0]
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        return (min(ys), min(xs))
    return sorted(lines, key=key)


def _ocr_image(gray: np.ndarray, run_real_ocr: bool):
    if not run_real_ocr:
        raise OCRDeferred("run_real_ocr=False，图片 OCR 推迟")
    if not paddle_available():
        raise OCRDependencyMissing("PaddleOCR 未安装，无法对图片做 OCR（RUN_REAL_OCR=1 但缺依赖）")

    ocr = get_paddle_ocr()
    if ocr is None:
        raise OCRDependencyMissing("PaddleOCR 未安装，无法对图片做 OCR（RUN_REAL_OCR=1 但缺依赖）")
    texts: list = []
    low_conf = False
    for chunk in _slice_long(gray):
        # PaddleOCR 接受 RGB 数组；转回 3 通道
        rgb = np.stack([chunk] * 3, axis=-1)
        res = ocr.ocr(rgb, cls=True)
        if not res or not res[0]:
            continue
        for line in _reading_order(res[0]):
            try:
                box, (txt, score) = line
            except (ValueError, TypeError):
                logger.warning("跳过无法解析的 OCR 行: %r", line)
                continue
            if score < 0.5:
                low_conf = True
            texts.append(txt)
    text = "\n".join(texts).strip()
    if not text:
        low_conf = True
    return text, low_conf


class ImageTableAdapter:
    """图片/规格表/长图适配器。"""
    kind = "image_table"

    def extract(self, path: str, run_real_ocr: bool = False) -> AdapterResult:
        from PIL import Image
        try:
            with Image.open(path) as pil:
                arr = np.array(pil.convert("RGB"))
            gray = _preprocess(arr)
            text, low_conf = _ocr_image(gray, run_real_ocr)
            # PP-Structure 表格抽取（共享模块，对原图 RGB 跑版面分析+表格识别）
            tables = []
            if run_real_ocr and paddle_available() and get_ppstructure() is not None:
                tables = _extract_tables_ppstructure(arr)
        except (OCRDeferred, OCRDependencyMissing):
            raise
        except Exception as e:
            logger.exception("图片适配器处理失败 %s: %s", type(e).__name__, e)
            raise RuntimeError(f"图片处理失败: {type(e).__name__}: {e}") from e
        meta = {"source": "image", "ocr": True, "low_conf": low_conf,
                "preprocess": "resize+gray+CLAHE"}
        if not tables:
            meta["table_pending"] = True
        return AdapterResult(
            text=text,
            meta=meta,
            tables=tables,
            low_conf=low_conf,
        )
