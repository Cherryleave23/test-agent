"""PP-StructureV3 表格识别共享模块（PaddleOCR 3.x API）。

性能优化：
  - mkldnn + monkey-patch（同 _paddle_ocr.py）
  - 接收预缩放图片（调用方负责缩放到 1600px）
  - 关闭不需要的模块（方向分类/矫正/公式/印章/图表）

3.x 变更：
  - PPStructure → PPStructureV3
  - __call__(img) → predict(img)
  - 结果格式：LayoutParsingResultV2，table_res_list[i]["pred_html"]
"""
from __future__ import annotations

import logging
import os
import threading
from html.parser import HTMLParser
from typing import Optional

logger = logging.getLogger(__name__)

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


class TableHTMLParser(HTMLParser):
    """从 PP-StructureV3 输出的 HTML 中解析单元格为二维数组，支持 colspan/rowspan。"""

    def __init__(self):
        super().__init__()
        self.rows: list = []
        self._occupied: set = set()
        self._row: int = -1
        self._cur_cell: str = ""
        self._cur_col: int = 0
        self._cur_span: tuple = (1, 1)
        self._in_cell = False

    @staticmethod
    def _int_attr(attrs_dict, key):
        try:
            return max(int(attrs_dict.get(key, "1")), 1)
        except (ValueError, TypeError):
            return 1

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row += 1
        elif tag in ("td", "th"):
            self._in_cell = True
            self._cur_cell = ""
            attrd = dict(attrs)
            colspan = self._int_attr(attrd, "colspan")
            rowspan = self._int_attr(attrd, "rowspan")
            self._cur_span = (colspan, rowspan)
            c = 0
            while (self._row, c) in self._occupied:
                c += 1
            self._cur_col = c

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            colspan, rowspan = self._cur_span
            r0, c0 = self._row, self._cur_col
            value = self._cur_cell.strip()
            for dr in range(rowspan):
                for dc in range(colspan):
                    rr, cc = r0 + dr, c0 + dc
                    self._occupied.add((rr, cc))
                    self._set_cell(rr, cc, value)
            self._in_cell = False
            self._cur_span = (1, 1)

    def _set_cell(self, r, c, value):
        while len(self.rows) <= r:
            self.rows.append([])
        row = self.rows[r]
        while len(row) <= c:
            row.append("")
        row[c] = value

    def handle_data(self, data):
        if self._in_cell:
            self._cur_cell += data


_pp_engine: Optional[object] = None
_pp_initialized: bool = False
_pp_lock = threading.Lock()


def get_ppstructure():
    """获取 PPStructureV3 引擎单例。

    配置：mkldnn + patch + 关闭不需要的模块。
    缺依赖返回 None。
    """
    global _pp_engine, _pp_initialized
    if _pp_initialized:
        return _pp_engine
    with _pp_lock:
        if _pp_initialized:
            return _pp_engine
        _pp_initialized = True
        try:
            from paddleocr import PPStructureV3
        except ImportError:
            logger.info("paddleocr 未安装，PPStructureV3 表格识别不可用")
            _pp_engine = None
            return _pp_engine

        # 复用 OCR 的 mkldnn patch
        from ._paddle_ocr import _patch_paddle_inference_config
        _patch_paddle_inference_config()

        try:
            _pp_engine = PPStructureV3(
                use_table_recognition=True,
                use_formula_recognition=False,
                use_chart_recognition=False,
                use_seal_recognition=False,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                lang="ch",
                engine="paddle_static",
                engine_config={
                    "device_type": "cpu",
                    "cpu_threads": 4,
                    "run_mode": "mkldnn",
                },
            )
            logger.info("PPStructureV3 引擎初始化成功 (mkldnn, 表格识别=ON)")
        except TypeError:
            try:
                _pp_engine = PPStructureV3(
                    use_table_recognition=True,
                    use_formula_recognition=False,
                    use_chart_recognition=False,
                    use_seal_recognition=False,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    lang="ch",
                )
                logger.info("PPStructureV3 引擎初始化成功（兼容模式）")
            except Exception as e:
                logger.warning("PPStructureV3 引擎初始化失败: %s: %s", type(e).__name__, e)
                _pp_engine = None
        except Exception as e:
            logger.warning("PPStructureV3 引擎初始化失败: %s: %s", type(e).__name__, e)
            _pp_engine = None
    return _pp_engine


def extract_tables(img_array) -> list:
    """用 PPStructureV3 从图像中抽取表格区域，返回 table dict 列表。

    注意：调用方应传入预缩放的图片（≤1600px），否则会很慢。
    """
    engine = get_ppstructure()
    if engine is None:
        return []
    out: list = []
    try:
        results = list(engine.predict(img_array))
        if not results:
            return out
        res = results[0]
        table_res_list = res.get("table_res_list", []) if isinstance(res, dict) else []
        if not table_res_list:
            return out
        for table_res in table_res_list:
            if not isinstance(table_res, dict):
                continue
            html = table_res.get("pred_html", "")
            if not html:
                continue
            parser = TableHTMLParser()
            parser.feed(html)
            cells = parser.rows if parser.rows else []
            cell_boxes = table_res.get("cell_box_list", [])
            bbox = _compute_bbox(cell_boxes)
            out.append({
                "html": html,
                "cells": cells,
                "bbox": bbox,
            })
    except Exception as e:
        logger.warning("PPStructureV3 表格抽取异常: %s: %s", type(e).__name__, e)
    return out


def _compute_bbox(cell_boxes) -> list:
    if not cell_boxes:
        return [0, 0, 0, 0]
    try:
        xs1, ys1, xs2, ys2 = [], [], [], []
        for box in cell_boxes:
            if len(box) >= 4:
                xs1.append(float(box[0]))
                ys1.append(float(box[1]))
                xs2.append(float(box[2]))
                ys2.append(float(box[3]))
        if not xs1:
            return [0, 0, 0, 0]
        return [min(xs1), min(ys1), max(xs2), max(ys2)]
    except (ValueError, TypeError):
        return [0, 0, 0, 0]


def reset():
    """重置引擎缓存（测试用）。"""
    global _pp_engine, _pp_initialized
    _pp_engine = None
    _pp_initialized = False
