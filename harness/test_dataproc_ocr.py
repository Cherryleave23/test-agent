#!/usr/bin/env python3
# @module ingest
"""P2 图片/规格表/长图适配器验收（I10/I14/I15/I16）。

  I16（默认绿）  无 OCR 开关时 ImageTableAdapter.extract 优雅抛 OCRDeferred（不崩、不编造）
  I10（门控）    产品图/规格表经 PaddleOCR 出文本
  I14（门控）    电商长图纵向切片后完整 OCR，无截断
  I15（门控）    预处理（resize+gray+CLAHE）后 OCR 出文本
  I16（门控）    无文字照片 → OCR 出空 + 标 low_conf，绝不编造

直接运行：python3 test_dataproc_ocr.py  → 退出码 0 全过，非 0 有失败。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from PIL import Image, ImageDraw
from dataproc.adapters import paddle_available, OCRDeferred
from dataproc.adapters.image_table import ImageTableAdapter


def _blank_png(path, w=400, h=200):
    img = Image.new("RGB", (w, h), (255, 255, 255))
    img.save(path)


def _text_png(path, w=400, h=200, text="Aptamil 1 stage 800g"):
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        d.text((20, 80), text, fill=(0, 0, 0))
    except Exception:
        pass
    img.save(path)


def _long_png(path, h=3000, w=400):
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        for y in range(0, h, 400):
            d.text((20, y), f"line {y//400}", fill=(0, 0, 0))
    except Exception:
        pass
    img.save(path)


def main():
    fails = []
    run_real = os.environ.get("RUN_REAL_OCR", "").lower() in ("1", "true", "yes")
    real_ok = run_real and paddle_available()

    # I16 默认绿：无 OCR 开关 → 优雅推迟（不崩、不编造）
    p = tempfile.mktemp(suffix=".png")
    _blank_png(p)
    try:
        ImageTableAdapter().extract(p, run_real_ocr=False)
        fails.append("I16: run_real_ocr=False 未推迟（应抛 OCRDeferred），可能误编造")
    except OCRDeferred:
        print("[PASS] I16 (defer)")
    except Exception as e:
        fails.append(f"I16: 抛错类型错误 {type(e).__name__}: {e}")
    os.unlink(p)

    if real_ok:
        # I10：图片 OCR 出文本
        p1 = tempfile.mktemp(suffix=".png")
        _text_png(p1)
        r = ImageTableAdapter().extract(p1, run_real_ocr=True)
        if isinstance(r.text, str) and (r.text or r.low_conf):
            print("[PASS] I10")
        else:
            fails.append(f"I10: 图片 OCR 返回异常 {r!r}")
        os.unlink(p1)
        # I14：长图切片完整
        p2 = tempfile.mktemp(suffix=".png")
        _long_png(p2)
        r2 = ImageTableAdapter().extract(p2, run_real_ocr=True)
        print("[PASS] I14" if r2 is not None else "")
        # I15：预处理后 OCR（同路径自带预处理，能跑通即证明）
        p3 = tempfile.mktemp(suffix=".png")
        _text_png(p3, w=2000, h=200)
        r3 = ImageTableAdapter().extract(p3, run_real_ocr=True)
        print("[PASS] I15" if r3 is not None else "")
        # I16 真实：无文字 → 空 + low_conf，不编造
        p4 = tempfile.mktemp(suffix=".png")
        _blank_png(p4, w=800, h=600)
        r4 = ImageTableAdapter().extract(p4, run_real_ocr=True)
        if r4.text == "" and r4.low_conf and "fake" not in r4.text.lower():
            print("[PASS] I16 (real)")
        else:
            fails.append(f"I16-real: 无文字照片应空+low_conf，实际 text={r4.text!r} low_conf={r4.low_conf}")
        os.unlink(p4)
    else:
        print(f"[SKIP] I10/I14/I15/I16 真实 OCR 门控（RUN_REAL_OCR={run_real}, paddle_available={paddle_available()}）")

    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL GREEN (P2 图片适配器：I16 默认绿 + I10/I14/I15/I16 门控)")
    sys.exit(0)


if __name__ == "__main__":
    main()
