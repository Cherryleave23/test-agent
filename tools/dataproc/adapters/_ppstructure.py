"""PP-StructureV3 表格识别模块（已禁用）。

表格识别功能已移除——PPStructureV3 加载 PP-OCRv5 server 模型导致处理极慢
（单张大图 >2 分钟），对产品图片场景不实用。

保留 TableHTMLParser 和 extract_tables 接口（返回空列表）以保持向后兼容，
未来如需重新启用表格识别可在此模块恢复 PPStructureV3 引擎。
"""
from __future__ import annotations

import logging
from html.parser import HTMLParser
from typing import Optional

logger = logging.getLogger(__name__)


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


def get_ppstructure() -> Optional[object]:
    """表格识别已禁用，始终返回 None。"""
    return None


def extract_tables(img_array) -> list:
    """表格识别已禁用，返回空列表。"""
    return []


def reset():
    """空操作（向后兼容）。"""
    pass
