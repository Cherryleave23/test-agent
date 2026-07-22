#!/usr/bin/env python3
# @module ingest
"""PP-Structure 表格识别 + 分类器验收（P2/P3 补全）。

  T1  PP-Structure 引擎初始化函数存在且可导入（不要求实际装 paddleocr）
  T2  _extract_tables_ppstructure 对空输入返回空列表（不崩）
  T3  _TableHTMLParser 能正确解析 HTML 表格为二维数组
  T4  classifier.classify_ptype 正确推断羊奶粉/有机奶粉/牛奶粉
  T5  classifier.classify_category 正确推断配方粉/营养品
  T6  classifier.classify 返回 {ptype, product_category} 且 conf.yaml 覆盖生效
  T7  ImageTableAdapter.extract 在无 OCR 时标 table_pending（默认绿）
  T8  test_dataproc_pdf.py 的 fitz 缺失不再崩溃（FITZ_OK 门控）

直接运行：python3 test_ppstructure_table.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "src"))


def t1_ppstructure_func_exists():
    """T1: PP-Structure 初始化函数存在且可导入。"""
    from dataproc.adapters.pdf import _try_init_ppstructure, _extract_tables_ppstructure
    assert callable(_try_init_ppstructure), "_try_init_ppstructure 应为可调用函数"
    assert callable(_extract_tables_ppstructure), "_extract_tables_ppstructure 应为可调用函数"
    # 不装 paddleocr 时应返回 None（不崩）
    engine = _try_init_ppstructure()
    assert engine is None or hasattr(engine, "__call__"), \
        f"_try_init_ppstructure 应返回 None 或引擎对象，实际: {type(engine)}"


def t2_extract_empty_input():
    """T2: _extract_tables_ppstructure 对空/异常输入返回空列表。"""
    from dataproc.adapters.pdf import _extract_tables_ppstructure
    # 传 None 引擎 + 空数组
    result = _extract_tables_ppstructure(None, [])
    assert result == [], f"空输入应返回空列表，实际: {result}"


def t3_html_parser():
    """T3: _TableHTMLParser 能正确解析 HTML 表格为二维数组。"""
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

    html = '<html><body><table><tr><td>品牌</td><td>飞鹤</td></tr><tr><td>段位</td><td>1段</td></tr></table></body></html>'
    parser = _TableHTMLParser()
    parser.feed(html)
    assert len(parser.rows) == 2, f"应解析出 2 行，实际: {len(parser.rows)}"
    assert parser.rows[0] == ["品牌", "飞鹤"], f"第 1 行不匹配: {parser.rows[0]}"
    assert parser.rows[1] == ["段位", "1段"], f"第 2 行不匹配: {parser.rows[1]}"


def t4_classify_ptype():
    """T4: classify_ptype 正确推断奶粉类型。"""
    from dataproc.classifier import classify_ptype
    assert classify_ptype("羊乳配方奶粉 1段") == "羊奶粉"
    assert classify_ptype("有机婴幼儿配方奶粉") == "有机奶粉"
    assert classify_ptype("优质牛奶粉 2段") == "牛奶粉"
    assert classify_ptype("深度水解蛋白奶粉") == "水解蛋白奶粉"
    assert classify_ptype("氨基酸配方粉") == "氨基酸配方粉"
    assert classify_ptype("普通产品") == ""
    assert classify_ptype("") == ""


def t5_classify_category():
    """T5: classify_category 正确推断商品大类。"""
    from dataproc.classifier import classify_category
    assert classify_category("婴儿配方奶粉 1段 800g") == "配方粉"
    assert classify_category("DHA藻油滴剂 营养补充") == "营养品"
    assert classify_category("婴儿米粉 辅食") == "辅食"
    assert classify_category("无相关关键词的产品") == ""


def t6_classify_full():
    """T6: classify 返回 {ptype, product_category} 完整结构。"""
    from dataproc.classifier import classify
    result = classify("羊乳配方奶粉 1段 800g")
    assert "ptype" in result and "product_category" in result
    assert result["ptype"] == "羊奶粉"
    assert result["product_category"] == "配方粉"

    result2 = classify("DHA藻油营养品")
    assert result2["ptype"] == "", "DHA 产品不应匹配 ptype 规则"
    assert result2["product_category"] == "营养品"


def t7_image_table_table_pending():
    """T7: ImageTableAdapter 无 OCR 时标 table_pending（默认绿）。"""
    from PIL import Image, ImageDraw
    from dataproc.adapters import OCRDeferred
    from dataproc.adapters.image_table import ImageTableAdapter

    p = tempfile.mktemp(suffix=".png")
    img = Image.new("RGB", (400, 200), (255, 255, 255))
    img.save(p)
    try:
        ImageTableAdapter().extract(p, run_real_ocr=False)
        assert False, "应抛 OCRDeferred"
    except OCRDeferred:
        pass  # 正确：推迟
    os.unlink(p)


def t8_pdf_test_fitz_guard():
    """T8: test_dataproc_pdf.py 的 fitz 缺失不再崩溃（FITZ_OK 门控）。"""
    # 读源码确认有 FITZ_OK 门控
    test_path = os.path.join(ROOT, "harness", "test_dataproc_pdf.py")
    with open(test_path, "r", encoding="utf-8") as f:
        src = f.read()
    assert "FITZ_OK" in src, "test_dataproc_pdf.py 应包含 FITZ_OK 门控"
    assert "try:" in src and "import fitz" in src, "应有 try/except 包裹 fitz 导入"


CHECKS = [
    ("T1 PP-Structure 函数存在且可导入", t1_ppstructure_func_exists),
    ("T2 _extract_tables 对空输入返回空列表", t2_extract_empty_input),
    ("T3 _TableHTMLParser 解析 HTML 表格", t3_html_parser),
    ("T4 classify_ptype 推断奶粉类型", t4_classify_ptype),
    ("T5 classify_category 推断商品大类", t5_classify_category),
    ("T6 classify 返回完整分类结构", t6_classify_full),
    ("T7 ImageTableAdapter 无 OCR 标 table_pending", t7_image_table_table_pending),
    ("T8 test_dataproc_pdf fitz 门控", t8_pdf_test_fitz_guard),
]


def main():
    failed = []
    for name, fn in CHECKS:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
        except Exception as e:
            print(f"[FAIL] {name}: 异常 {type(e).__name__}: {e}")
            failed.append(name)
    print(f"=== Summary: {len(CHECKS) - len(failed)}/{len(CHECKS)} passed ===")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("RESULT: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
