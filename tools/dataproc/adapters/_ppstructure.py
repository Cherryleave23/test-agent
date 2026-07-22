"""PP-StructureV3 表格识别共享模块（PaddleOCR 3.x API）。

消除 pdf.py / image_table.py 中的 _TableHTMLParser 和 PP-Structure 初始化逻辑重复。
提供：
  - TableHTMLParser: HTML 表格 → 二维 cells 数组
  - get_ppstructure(): PPStructureV3 引擎单例（首次调用初始化，后续复用）
  - extract_tables(img_array): 从图像抽取表格区域

3.x 变更：
  - PPStructure → PPStructureV3
  - __call__(img) → predict(img)
  - 结果格式：LayoutParsingResultV2，table_res_list[i]["pred_html"]

零 src.*，零外部硬依赖（paddleocr 缺失时返回空/None）。
"""
from __future__ import annotations

import logging
import os
import threading
from html.parser import HTMLParser
from typing import Optional

logger = logging.getLogger(__name__)

# 禁用模型源检查，加速初始化
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


class TableHTMLParser(HTMLParser):
    """从 PP-StructureV3 输出的 HTML 中解析单元格为二维数组，支持 colspan/rowspan。"""

    def __init__(self):
        super().__init__()
        self.rows: list = []          # 最终输出：二维 cells 数组（矩形）
        self._occupied: set = set()   # 已被占用的 (row, col)
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
            # 找当前行下一个未被占用的列（跳过上方 rowspan 占位）
            c = 0
            while (self._row, c) in self._occupied:
                c += 1
            self._cur_col = c

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            colspan, rowspan = self._cur_span
            r0, c0 = self._row, self._cur_col
            value = self._cur_cell.strip()
            # 把值写入跨度覆盖的每个位置，保证二维数组列对齐
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


# 模块级单例：避免每次调用重新初始化 PPStructureV3 引擎
_pp_engine: Optional[object] = None
_pp_initialized: bool = False
_pp_lock = threading.Lock()


def get_ppstructure():
    """获取 PPStructureV3 引擎单例。

    使用 PaddleOCR 3.x API：PPStructureV3(engine="paddle_static", engine_config={...})
    与 PaddleOCR 相同的 engine 配置，避免 Windows PIR/oneDNN 问题。
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
                    "run_mode": "paddle",
                },
            )
            logger.info("PPStructureV3 引擎初始化成功 (engine=paddle_static, run_mode=paddle)")
        except TypeError:
            # 兼容：旧版不支持 engine/engine_config
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

    3.x 结果格式：LayoutParsingResultV2
      - result["table_res_list"]: list of dict, each has:
        - "pred_html": 表格 HTML 字符串
        - "cell_box_list": 单元格坐标列表

    每个 table dict 包含：
    - html: 表格 HTML 结构字符串
    - cells: [[row_val, ...], ...] 二维数组（从 HTML 解析）
    - bbox: [x1, y1, x2, y2] 表格区域坐标（从 cell_box_list 推算）

    缺引擎或异常时返回空列表（不阻断 OCR 文本）。
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
        # 3.x: table_res_list
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
            # 从 cell_box_list 推算 bbox
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
    """从 cell_box_list 推算表格整体 bbox。"""
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
