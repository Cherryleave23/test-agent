#!/usr/bin/env python3
# @module ingest
"""PP-Structure 表格识别 + 分类器验收（P2/P3 补全）。

  T1  PP-Structure 共享模块可导入（get_ppstructure / extract_tables / TableHTMLParser）
  T2  extract_tables 对 None 引擎返回空列表（不崩）
  T3  TableHTMLParser（从共享模块导入）能正确解析 HTML 表格为二维数组
  T4  classifier.classify_ptype 正确推断羊奶粉/有机奶粉/牛奶粉
  T5  classifier.classify_category 正确推断配方粉/营养品
  T6  classifier.classify 返回 {ptype, product_category} 完整结构
  T7  ImageTableAdapter.extract 在无 OCR 时抛 OCRDeferred
  T8  test_dataproc_pdf.py 在无 fitz 时行为正确（不崩，退出码 0）
  T9  classifier conf.yaml 覆盖路径生效（自定义 ptype→category 映射）
  T10 classifier conf.yaml 缓存生效（多次调用不重复读文件）

直接运行：python3 test_ppstructure_table.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
import tempfile
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "src"))


def t1_ppstructure_shared_module():
    """T1: PP-Structure 共享模块可导入。"""
    from dataproc.adapters._ppstructure import (
        get_ppstructure, extract_tables, TableHTMLParser, reset
    )
    assert callable(get_ppstructure), "get_ppstructure 应为可调用函数"
    assert callable(extract_tables), "extract_tables 应为可调用函数"
    assert callable(TableHTMLParser), "TableHTMLParser 应为可调用类"
    assert callable(reset), "reset 应为可调用函数"
    # 不装 paddleocr 时应返回 None（不崩）
    reset()
    engine = get_ppstructure()
    assert engine is None or hasattr(engine, "__call__"), \
        f"get_ppstructure 应返回 None 或引擎对象，实际: {type(engine)}"


def t2_extract_empty_input():
    """T2: extract_tables 对无引擎时返回空列表。"""
    from dataproc.adapters._ppstructure import extract_tables, reset
    reset()  # 确保引擎被清除
    # 无 paddleocr 时 engine=None，extract_tables 应返回 []
    result = extract_tables([])
    assert result == [], f"无引擎时应返回空列表，实际: {result}"


def t3_html_parser_from_shared():
    """T3: TableHTMLParser（从共享模块导入）能正确解析 HTML 表格。"""
    from dataproc.adapters._ppstructure import TableHTMLParser
    html = '<html><body><table><tr><td>品牌</td><td>飞鹤</td></tr><tr><td>段位</td><td>1段</td></tr></table></body></html>'
    parser = TableHTMLParser()
    parser.feed(html)
    assert len(parser.rows) == 2, f"应解析出 2 行，实际: {len(parser.rows)}"
    assert parser.rows[0] == ["品牌", "飞鹤"], f"第 1 行不匹配: {parser.rows[0]}"
    assert parser.rows[1] == ["段位", "1段"], f"第 2 行不匹配: {parser.rows[1]}"


def t3b_html_parser_colspan_rowspan():
    """T3b: TableHTMLParser 支持 colspan/rowspan（P2-N5 补充测试）。"""
    from dataproc.adapters._ppstructure import TableHTMLParser

    # colspan 测试
    html_colspan = '''<table>
    <tr><td colspan="2">合并表头</td></tr>
    <tr><td>A</td><td>B</td></tr>
    </table>'''
    parser = TableHTMLParser()
    parser.feed(html_colspan)
    assert len(parser.rows) == 2, f"colspan: 应解析出 2 行，实际: {len(parser.rows)}"
    assert len(parser.rows[0]) == 2, f"colspan: 行应有 2 列，实际: {len(parser.rows[0])}"
    assert parser.rows[0][0] == "合并表头", f"colspan: 内容应填充，实际: {parser.rows[0]}"
    assert parser.rows[1] == ["A", "B"], f"colspan: 第二行不匹配: {parser.rows[1]}"

    # rowspan 测试
    html_rowspan = '''<table>
    <tr><td rowspan="2">跨行</td><td>1</td></tr>
    <tr><td>2</td></tr>
    </table>'''
    parser2 = TableHTMLParser()
    parser2.feed(html_rowspan)
    assert len(parser2.rows) == 2, f"rowspan: 应解析出 2 行，实际: {len(parser2.rows)}"
    assert len(parser2.rows[0]) == 2, f"rowspan: 第一行应有 2 列，实际: {len(parser2.rows[0])}"
    assert parser2.rows[0] == ["跨行", "1"], f"rowspan: 第一行不匹配: {parser2.rows[0]}"
    assert parser2.rows[1] == ["跨行", "2"], f"rowspan: 第二行应继承跨行值: {parser2.rows[1]}"


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
    """T7: ImageTableAdapter 无 OCR 时抛 OCRDeferred。"""
    from PIL import Image
    from dataproc.adapters import OCRDeferred
    from dataproc.adapters.image_table import ImageTableAdapter

    # 使用 mkstemp 替代 mktemp（更安全）
    fd, p = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img = Image.new("RGB", (400, 200), (255, 255, 255))
    img.save(p)
    try:
        ImageTableAdapter().extract(p, run_real_ocr=False)
        assert False, "应抛 OCRDeferred"
    except OCRDeferred:
        pass  # 正确：推迟
    finally:
        os.unlink(p)


def t8_pdf_test_behavior():
    """T8: test_dataproc_pdf.py 在无 fitz 时行为正确（实际运行，退出码 0）。"""
    test_path = os.path.join(ROOT, "harness", "test_dataproc_pdf.py")
    result = subprocess.run(
        [sys.executable, test_path],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "RUN_REAL_OCR": "0"},
    )
    assert result.returncode == 0, \
        f"test_dataproc_pdf.py 应退出码 0，实际 {result.returncode}。\nstdout: {result.stdout}\nstderr: {result.stderr}"


def t9_conf_yaml_override():
    """T9: classifier conf.yaml 覆盖路径生效。"""
    import yaml as _yaml
    from dataproc.classifier import classify, load_category_overrides
    import dataproc.classifier as cls_mod

    # 创建临时 conf.yaml，自定义 ptype→category 映射
    conf_dir = tempfile.mkdtemp()
    conf_path = os.path.join(conf_dir, "conf.yaml")
    with open(conf_path, "w", encoding="utf-8") as f:
        _yaml.dump({"product_categories": {"牛奶粉": "定制类别"}}, f, allow_unicode=True)

    try:
        overrides = load_category_overrides(conf_path)
        assert "牛奶粉" in overrides, f"conf.yaml 应包含牛奶粉映射，实际: {overrides}"
        assert overrides["牛奶粉"] == "定制类别"

        result = classify("优质牛奶粉 2段", conf_path)
        assert result["ptype"] == "牛奶粉"
        assert result["product_category"] == "定制类别", f"conf.yaml 覆盖应生效，实际: {result['product_category']}"
    finally:
        # P2-19: 清理 classifier 缓存，避免影响后续测试
        cls_mod._overrides_cache = {}
        cls_mod._overrides_path = None
        cls_mod._overrides_mtime = 0.0
        os.unlink(conf_path)
        os.rmdir(conf_dir)


def t10_conf_cache():
    """T10: classifier conf.yaml 缓存生效（多次调用不重复读文件）。"""
    import yaml as _yaml
    from dataproc.classifier import classify
    import dataproc.classifier as cls_mod

    conf_dir = tempfile.mkdtemp()
    conf_path = os.path.join(conf_dir, "conf.yaml")
    with open(conf_path, "w", encoding="utf-8") as f:
        _yaml.dump({"product_categories": {"牛奶粉": "缓存测试"}}, f, allow_unicode=True)

    try:
        # 第一次调用：加载并缓存
        classify("牛奶粉", conf_path)
        assert cls_mod._overrides_path == conf_path, "缓存路径应设置"
        first_mtime = cls_mod._overrides_mtime

        # 第二次调用：应命中缓存（不重新读文件）
        classify("牛奶粉", conf_path)
        assert cls_mod._overrides_mtime == first_mtime, "mtime 未变时应命中缓存"
    finally:
        # P2-19: 清理 classifier 缓存，避免影响后续测试
        cls_mod._overrides_cache = {}
        cls_mod._overrides_path = None
        cls_mod._overrides_mtime = 0.0
        os.unlink(conf_path)
        os.rmdir(conf_dir)


CHECKS = [
    ("T1 PP-Structure 共享模块可导入", t1_ppstructure_shared_module),
    ("T2 extract_tables 无引擎返回空列表", t2_extract_empty_input),
    ("T3 TableHTMLParser 从共享模块解析 HTML", t3_html_parser_from_shared),
    ("T3b TableHTMLParser colspan/rowspan 支持", t3b_html_parser_colspan_rowspan),
    ("T4 classify_ptype 推断奶粉类型", t4_classify_ptype),
    ("T5 classify_category 推断商品大类", t5_classify_category),
    ("T6 classify 返回完整分类结构", t6_classify_full),
    ("T7 ImageTableAdapter 无 OCR 抛 OCRDeferred", t7_image_table_table_pending),
    ("T8 test_dataproc_pdf 行为测试（退出码 0）", t8_pdf_test_behavior),
    ("T9 conf.yaml 覆盖路径生效", t9_conf_yaml_override),
    ("T10 conf.yaml 缓存生效", t10_conf_cache),
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
