"""PP-Structure 表格识别共享模块。

消除 pdf.py / image_table.py 中的 _TableHTMLParser 和 PP-Structure 初始化逻辑重复。
提供：
  - TableHTMLParser: HTML 表格 → 二维 cells 数组
  - get_ppstructure(): 引擎单例（首次调用初始化，后续复用）
  - extract_tables(img_array): 从图像抽取表格区域

零 src.*，零外部硬依赖（paddleocr 缺失时返回空/None）。
"""
from __future__ import annotations

import logging
import threading
from html.parser import HTMLParser
from typing import Optional

logger = logging.getLogger(__name__)


class TableHTMLParser(HTMLParser):
    """从 PP-Structure 输出的 HTML 中解析单元格为二维数组，支持 colspan/rowspan。"""

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


# 模块级单例：避免每次调用重新初始化 PP-Structure 引擎
_pp_engine: Optional[object] = None
_pp_initialized: bool = False
_pp_lock = threading.Lock()


def get_ppstructure():
    """获取 PP-Structure 引擎单例。

    首次调用尝试初始化；缺依赖返回 None。后续调用直接返回缓存实例。
    线程安全：通过 _pp_lock 保护全局可变状态。
    """
    global _pp_engine, _pp_initialized
    if _pp_initialized:
        return _pp_engine
    with _pp_lock:
        # 双重检查，避免锁内重复初始化
        if _pp_initialized:
            return _pp_engine
        _pp_initialized = True
        try:
            from paddleocr import PPStructure
            _pp_engine = PPStructure(
                layout=True, table=True,
                ocr=True, structure_version="PP-StructureV2",
            )
            logger.info("PP-Structure 引擎初始化成功")
        except ImportError:
            logger.info("paddleocr 未安装，PP-Structure 表格识别不可用")
            _pp_engine = None
        except TypeError:
            # PaddleOCR 3.x API 变更，尝试简化参数
            try:
                from paddleocr import PPStructure
                _pp_engine = PPStructure()
                logger.info("PP-Structure 引擎初始化成功（兼容模式）")
            except Exception as e:
                logger.warning("PP-Structure 引擎初始化失败: %s: %s", type(e).__name__, e)
                _pp_engine = None
        except Exception as e:
            logger.warning("PP-Structure 引擎初始化失败: %s: %s", type(e).__name__, e)
            _pp_engine = None
    return _pp_engine


def extract_tables(img_array) -> list:
    """用 PP-Structure 从图像中抽取表格区域，返回 table dict 列表。

    每个 table dict 包含：
    - html: 表格 HTML 结构字符串
    - cells: [[row_val, ...], ...] 二维数组（从 HTML 解析）
    - bbox: [x1, y1, x2, y2] 表格区域坐标

    缺引擎或异常时返回空列表（不阻断 OCR 文本）。
    """
    engine = get_ppstructure()
    if engine is None:
        return []
    out: list = []
    try:
        results = engine(img_array)
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
            parser = TableHTMLParser()
            parser.feed(html)
            cells = parser.rows if parser.rows else []
            out.append({
                "html": html,
                "cells": cells,
                "bbox": bbox,
            })
    except Exception as e:
        logger.warning("PP-Structure 表格抽取异常: %s: %s", type(e).__name__, e)
    return out


def reset():
    """重置引擎缓存（测试用）。"""
    global _pp_engine, _pp_initialized
    _pp_engine = None
    _pp_initialized = False
