"""图片/规格表/电商长图适配器：PaddleOCR 3.x + 阅读顺序 + 长图切片。

PP-OCRv6 优化（不做 1600px 预缩放，否则精度暴跌）：
  - 正常图片：传文件路径给 predict()，PaddleOCR 3.x 内置 max_side_limit=4000 自动缩放
  - 长图（高>3×宽）：按原始分辨率切片（每片 1200px），传 numpy 给 predict()

零 src.*。无文字/低置信标 low_conf，绝不编造。"""
from __future__ import annotations

import logging

import numpy as np

from . import OCRDeferred, OCRDependencyMissing, AdapterResult, paddle_available
from ._paddle_ocr import get_paddle_ocr

logger = logging.getLogger(__name__)

# 长图纵向切片阈值：高 > SLICE_RATIO*宽 视为长图
SLICE_RATIO = 3.0
SLICE_H = 1200          # 单切片像素高
SLICE_OVERLAP = 120     # 切片重叠，避免切断行


def _slice_long_rgb(arr: np.ndarray):
    """长图（高>宽数倍）纵向切片，带重叠避免切断。RGB 输入/输出。"""
    h, w = arr.shape[:2]
    if h <= SLICE_RATIO * w:
        yield arr
        return
    step = SLICE_H - SLICE_OVERLAP
    last_y = 0
    for y in range(0, max(h - SLICE_H, 0) + 1, step):
        yield arr[y:y + SLICE_H]
        last_y = y
    # 末片收尾：仅当还存在未被覆盖的尾部时补一片
    if h - SLICE_H > last_y:
        yield arr[h - SLICE_H:h]


def _reading_order(lines):
    """按阅读顺序排序：(min_y, min_x)。"""
    def key(ln):
        box = ln[0]
        try:
            ys = [float(p[1]) for p in box]
            xs = [float(p[0]) for p in box]
        except (TypeError, IndexError, ValueError):
            return (0, 0)
        return (min(ys), min(xs))
    return sorted(lines, key=key)


def _ocr_predict(ocr, input_data):
    """调用 PaddleOCR predict()，兼容文件路径和 numpy 数组输入。

    3.x: ocr.predict(input) → list[OCRResult]
    2.x: ocr.ocr(input, cls=True)
    """
    try:
        return list(ocr.predict(input_data))
    except (TypeError, AttributeError):
        try:
            return ocr.ocr(input_data, cls=True)
        except TypeError:
            return ocr.ocr(input_data)


def _extract_text_from_results(results):
    """从 OCR 结果中提取文本行，返回 (text, low_conf)。"""
    texts: list = []
    low_conf = False
    if results:
        lines = _extract_lines(results)
        for line in _reading_order(lines):
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


class ImageTableAdapter:
    """图片/规格表/长图适配器。

    正常图片传文件路径给 PaddleOCR（内置 4000px 自动缩放，不做预缩放）；
    长图按原始分辨率切片后逐片 OCR。
    """
    kind = "image_table"

    def extract(self, path: str, run_real_ocr: bool = False) -> AdapterResult:
        from PIL import Image
        try:
            if not run_real_ocr:
                raise OCRDeferred("run_real_ocr=False，图片 OCR 推迟")
            if not paddle_available():
                raise OCRDependencyMissing(
                    "PaddleOCR 未安装，无法对图片做 OCR（RUN_REAL_OCR=1 但缺依赖）")

            ocr = get_paddle_ocr()
            if ocr is None:
                raise OCRDependencyMissing(
                    "PaddleOCR 未安装，无法对图片做 OCR（RUN_REAL_OCR=1 但缺依赖）")

            # 读取图片尺寸判断是否长图
            with Image.open(path) as pil:
                w, h = pil.size
            is_long = h > SLICE_RATIO * w

            if is_long:
                # 长图：按原始分辨率切片后逐片 OCR（切片高度 1200px，无需额外缩放）
                with Image.open(path) as pil:
                    arr = np.array(pil.convert("RGB"))
                text_parts: list = []
                low_conf = False
                for chunk in _slice_long_rgb(arr):
                    results = _ocr_predict(ocr, chunk)
                    t, lc = _extract_text_from_results(results)
                    if t:
                        text_parts.append(t)
                    low_conf = low_conf or lc
                text = "\n".join(text_parts).strip()
                if not text:
                    low_conf = True
            else:
                # 正常图片：传文件路径，PaddleOCR 3.x 内置 max_side_limit=4000 自动缩放
                results = _ocr_predict(ocr, path)
                text, low_conf = _extract_text_from_results(results)

        except (OCRDeferred, OCRDependencyMissing):
            raise
        except Exception as e:
            logger.exception("图片适配器处理失败 %s: %s", type(e).__name__, e)
            raise RuntimeError(f"图片处理失败: {type(e).__name__}: {e}") from e

        meta = {"source": "image", "ocr": True, "low_conf": low_conf}
        return AdapterResult(
            text=text,
            meta=meta,
            tables=[],
            low_conf=low_conf,
        )
