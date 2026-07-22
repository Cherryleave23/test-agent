"""图片/规格表/电商长图适配器：opencv 预处理 + PaddleOCR + 阅读顺序 + 长图切片。
零 src.*。无文字/低置信标 low_conf，绝不编造。"""
from __future__ import annotations

import os

import numpy as np

from . import OCRDeferred, OCRDependencyMissing, AdapterResult, paddle_available

# 长图纵向切片阈值：高 > SLICE_RATIO*宽 视为长图
SLICE_RATIO = 3.0
SLICE_H = 1200          # 单切片像素高
SLICE_OVERLAP = 120     # 切片重叠，避免切断行


def _preprocess(img: np.ndarray) -> np.ndarray:
    """轻量预处理：缩放（限长边）→ 灰度 → CLAHE 增强对比。"""
    h, w = img.shape[:2]
    long_side = max(h, w)
    if long_side > 1600:
        scale = 1600 / long_side
        img = np.asarray(__import__("cv2").resize(
            img, (int(w * scale), int(h * scale))))
    gray = __import__("cv2").cvtColor(img, __import__("cv2").COLOR_RGB2GRAY)
    clahe = __import__("cv2").createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _slice_long(gray: np.ndarray):
    """长图（高>宽数倍）纵向切片，带重叠避免切断。"""
    h, w = gray.shape
    if h <= SLICE_RATIO * w:
        yield gray
        return
    step = SLICE_H - SLICE_OVERLAP
    for y in range(0, max(h - SLICE_H, 0) + 1, step):
        yield gray[y:y + SLICE_H]
    if h - SLICE_H > 0:
        yield gray[h - SLICE_H:h]  # 末片收尾


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

    from paddleocr import PaddleOCR
    ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    texts: list = []
    low_conf = False
    for chunk in _slice_long(gray):
        # PaddleOCR 接受 RGB 数组；转回 3 通道
        rgb = np.stack([chunk] * 3, axis=-1)
        res = ocr.ocr(rgb, cls=True)
        if not res or not res[0]:
            continue
        for line in _reading_order(res[0]):
            box, (txt, score) = line
            if score < 0.5:
                low_conf = True
            texts.append(txt)
    text = "\n".join(texts).strip()
    if not text:
        low_conf = True
    return text, low_conf


def _extract_tables_from_image(img_array, run_real_ocr: bool) -> list:
    """用 PP-Structure 从图片中抽取表格区域，返回 table dict 列表。

    在 OCR 文本提取之后调用，对原图（RGB）跑 PP-Structure 版面分析+表格识别。
    缺依赖时返回空列表（调用方标 table_pending）。
    """
    if not run_real_ocr or not paddle_available():
        return []
    try:
        from paddleocr import PPStructure
        from html.parser import HTMLParser

        class _TableHTMLParser(HTMLParser):
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

        engine = PPStructure(show_log=False, layout=True, table=True,
                             ocr=True, structure_version="PP-StructureV2")
        results = engine(img_array)
        tables: list = []
        if not results:
            return tables
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
            parser = _TableHTMLParser()
            parser.feed(html)
            cells = parser.rows if parser.rows else []
            tables.append({"html": html, "cells": cells, "bbox": bbox})
        return tables
    except Exception:
        return []


class ImageTableAdapter:
    """图片/规格表/长图适配器。"""
    kind = "image_table"

    def extract(self, path: str, run_real_ocr: bool = False) -> AdapterResult:
        import cv2
        from PIL import Image
        pil = Image.open(path).convert("RGB")
        arr = np.array(pil)
        gray = _preprocess(arr)
        text, low_conf = _ocr_image(gray, run_real_ocr)
        # PP-Structure 表格抽取（对原图 RGB 跑版面分析+表格识别）
        tables = _extract_tables_from_image(arr, run_real_ocr)
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
