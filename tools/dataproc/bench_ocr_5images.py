"""PaddleOCR 官方适配器实测 + 多图性能基准。

用法:
    python tools/dataproc/bench_ocr_5images.py [IMG_DIR]

默认测试集: tools/dataproc/bench_images/img1.jpg ... img5.jpg（你发送的 5 张母婴产品图，已持久化）
依赖: paddleocr[all]>=3.7.0 + PaddlePaddle 3.x（官方完整安装）

输出: 每图识别文本 + 单图耗时；汇总总耗时/平均耗时/吞吐（多图提取性能基准）。
"""
from __future__ import annotations

import os
import sys
import time
import glob

# 让 tools.dataproc.adapters 可被导入
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.dataproc.adapters.image_table import ImageTableAdapter  # noqa: E402


def main():
    # 默认使用持久化基准图集（含你发送的 5 张母婴产品图），也可用 sys.argv[1] 覆盖
    default_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_images")
    img_dir = sys.argv[1] if len(sys.argv) > 1 else default_dir
    imgs = sorted(glob.glob(os.path.join(img_dir, "*.jpg"))) + \
           sorted(glob.glob(os.path.join(img_dir, "*.png")))
    if not imgs:
        print(f"[ERROR] 未找到测试图片: {img_dir}")
        return 1

    print(f"测试集: {img_dir}  ({len(imgs)} 张)\n")
    print("=" * 100)

    adapter = ImageTableAdapter()

    # 引擎初始化计时（一次性）
    t0 = time.perf_counter()
    from tools.dataproc.adapters._paddle_ocr import get_paddle_ocr
    get_paddle_ocr()
    init_dt = time.perf_counter() - t0
    print(f"[引擎初始化] 一次性耗时 {init_dt:.2f}s\n")

    rows = []
    total_chars = 0
    total_dt = 0.0
    for i, path in enumerate(imgs, 1):
        fname = os.path.basename(path)
        # 尺寸
        try:
            from PIL import Image
            with Image.open(path) as pil:
                w, h = pil.size
        except Exception:
            w = h = 0

        t0 = time.perf_counter()
        try:
            res = adapter.extract(path, run_real_ocr=True)
            text = res.text or ""
            low_conf = res.low_conf
            err = ""
        except Exception as e:
            text = ""
            low_conf = True
            err = f"{type(e).__name__}: {e}"
        dt = time.perf_counter() - t0

        total_dt += dt
        n = len(text)
        total_chars += n
        rows.append((fname, w, h, n, low_conf, dt, err))

        print(f"[{i}/{len(imgs)}] {fname}  {w}x{h}  {n}字  low_conf={low_conf}  {dt:.2f}s")
        if err:
            print(f"    !! 错误: {err}")
        # 打印前若干识别文本（验证质量）
        snippet = text.replace("\n", " ⏎ ")[:300]
        print(f"    文本: {snippet}")
        print("-" * 100)

    n = len(rows)
    avg = total_dt / n if n else 0
    tput = n / total_dt if total_dt else 0
    print("\n" + "=" * 100)
    print("【多图提取性能基准】")
    print(f"  图片数            : {n}")
    print(f"  引擎初始化(一次性): {init_dt:.2f}s")
    print(f"  OCR 总耗时        : {total_dt:.2f}s")
    print(f"  平均单图耗时      : {avg:.2f}s")
    print(f"  吞吐              : {tput:.3f} 图/s  ({1/tput:.2f}s/图 反算)")
    print(f"  识别总字符数      : {total_chars}")
    print(f"  GPU?              : DATAPROC_OCR_DEVICE={os.environ.get('DATAPROC_OCR_DEVICE') or '(未设置→官方自动检测)'}")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
