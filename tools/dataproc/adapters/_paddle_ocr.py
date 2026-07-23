"""共享 PaddleOCR 引擎单例：线程安全双重检查锁定。

官方 PP-OCRv6 运行方式（精度优先）：
  https://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv6/PP-OCRv6.html#5-quick-start

官方 Quick Start（verbatim）：
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    result = ocr.predict("input.jpg")

要点（与官方保持一致，不做任何精度折衷的"优化"）：
  - 默认模型即 PP-OCRv6_medium（paddleocr>=3.7.0 的默认项；lang="ch" 也解析到 v6_medium）。
    检测 Hmean 86.2% / 识别 83.2%，单模型 50 语言（中/英/日 + 46 拉丁），34.5M 参数。
  - 关闭文档方向分类 / 文档扭曲矫正 / 文本行方向分类 三项（官方三项全关，产品图无需）。
  - 使用官方默认 paddle 动态图引擎（engine="paddle"），自动选用 GPU，否则回退 CPU。
  - 必须传文件路径给 predict()；PaddleOCR 3.x 内置 max_side_limit=4000 自动缩放。

框架兼容性补丁 _patch_paddle_inference_config（精度中立，非"算法优化"）：
  PaddlePaddle 3.3.1 的 onednn(mkldnn) 执行器对 PP-OCRv6 检测后处理用到
  pir::ArrayAttribute<pir::DoubleAttribute> 类型存在 bug：
      NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support
        .../onednn/onednn_instruction.cc:116
  该 bug 在 CPU(onednn) 上会出现（Windows / Linux 均可能触发），官方 enable_new_ir=False
  或 delete_pass 均无法绕过；唯一有效手段是把推理 Config 的 set_optimization_level 强制为 0，
  禁用触发该 bug 的图优化 pass。此 patch 仅关掉图优化、不改变数值结果（精度零损失，最多略慢），
  因此与官方精度方案不冲突，必须保留，否则官方引擎在 CPU 上直接抛异常、无法 OCR。
  （GPU 走 CUDA 执行器不经过 onednn，无此 bug，可不打 patch。）

device 覆盖（可选，精度中立，官方 engine_config API）：
  - 环境变量 DATAPROC_OCR_DEVICE=cpu|gpu 可强制设备；不设置则交给官方自动检测。
  - 仅用于端侧部署（门店机无 GPU 时显式锁 CPU，或在有 GPU 的机器上省显存）。

已移除 / 确认非冲突的"优化"：
  - 自定义 engine_config={"device_type":"cpu","cpu_threads":4,"run_mode":"mkldnn"}：
    旧的非官方 paddle_static+mkldnn 写法，已改为官方默认 paddle 动态图引擎（内部仍走 onednn）。
  - 更早的"56s→5s" 1600px 预缩放优化：此前已刻意删除（预缩放致精度暴跌，与官方冲突）。
  - 长图纵向切片(SLICE_H=1200) 与 tbpu 低置信阈值(0.5)：精度中立/改善，与官方不冲突，保留。

性能基准（见 bench_ocr_5images.py，5 张母婴产品图 1600x2133，官方 paddle 引擎 + onednn 补丁，CPU）：
  v6_medium + 文件路径：平均 56.6s/图，5 图共 283.1s，0.018 图/s，识别 1584 字
  （精度优先的代价；官方数据 A100 paddle 引擎约 0.29s/图，有 GPU 时大幅加速）。
"""
from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

# 在导入 paddle 前设置环境变量（辅助 PIR 禁用，配合下方 monkey-patch 绕过 onednn bug）
os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")
os.environ.setdefault("FLAGS_pir_apply_inplace_pass", "0")


def _patch_paddle_inference_config():
    """Monkey-patch paddle.inference.Config：把 set_optimization_level 强制设为 0。

    绕过 PaddlePaddle 3.3.1 onednn(mkldnn) 执行器对 PP-OCRv6 检测后处理的 PIR bug：
        NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support
          .../onednn/onednn_instruction.cc:116
    官方 engine_config 中 enable_new_ir=False / delete_pass 均无法绕过；只有将
    set_optimization_level 强制为 0 才有效。该 patch 仅禁用图优化 pass，不改变数值结果
    （精度零损失，最多略慢），因此与官方精度方案不冲突——它是框架兼容性补丁，不是精度优化。
    仅在 CPU(onednn) 路径需要；GPU(CUDA) 不经过 onednn，无此 bug。
    """
    try:
        import paddle.inference as paddle_inference

        _orig_set_opt = paddle_inference.Config.set_optimization_level

        def _patched_set_opt(self, level=3):
            return _orig_set_opt(self, 0)

        paddle_inference.Config.set_optimization_level = _patched_set_opt
        logger.info("Paddle inference Config patched: optimization_level=0 (绕过 onednn PIR bug)")
    except Exception as e:
        logger.warning("Failed to patch paddle.inference.Config: %s: %s", type(e).__name__, e)


# 模块级单例
_ocr_engine = None
_ocr_initialized = False
_ocr_lock = threading.Lock()


def get_paddle_ocr():
    """获取 PaddleOCR 引擎单例（线程安全，双重检查锁定）。

    严格按官方 PP-OCRv6 Quick Start 构造：默认 PP-OCRv6_medium + 关闭三项分类/矫正，
    使用官方默认 paddle 动态图引擎（自动选 GPU，否则 CPU）。
    在 CPU(onednn) 路径上施加精度中立的框架兼容补丁，否则官方引擎会因 PIR bug 直接抛异常。
    device 经 DATAPROC_OCR_DEVICE（cpu/gpu）可选覆盖，不设置则官方自动检测。
    """
    global _ocr_engine, _ocr_initialized
    if _ocr_initialized:
        return _ocr_engine
    with _ocr_lock:
        if _ocr_initialized:
            return _ocr_engine
        _ocr_initialized = True
        try:
            from paddleocr import PaddleOCR
        except ImportError:
            logger.info("paddleocr 未安装，PaddleOCR 引擎不可用")
            _ocr_engine = None
            return _ocr_engine

        # device 选择：cpu（默认，onednn 路径需打补丁）/ gpu（CUDA，无需补丁）
        device = (os.environ.get("DATAPROC_OCR_DEVICE") or "").lower()
        if device != "gpu":
            # CPU 经 onednn 执行器，必须打补丁绕过 PIR bug（精度中立）
            _patch_paddle_inference_config()

        # 官方 PP-OCRv6 运行方式（精度优先，默认 PP-OCRv6_medium）：
        #   三项全关 + 官方默认 paddle 动态图引擎。
        # device 可选覆盖（精度中立）：仅当显式设置 DATAPROC_OCR_DEVICE 时传入 engine_config。
        kwargs = dict(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            lang="ch",
        )
        if device in ("cpu", "gpu"):
            kwargs["engine_config"] = {"device_type": device}

        try:
            _ocr_engine = PaddleOCR(**kwargs)
            logger.info(
                "PaddleOCR 3.x 引擎初始化成功（官方 PP-OCRv6_medium，device=%s）",
                device or "auto",
            )
        except TypeError:
            # 兼容：极老版本不支持 use_textline_orientation 等参数，退回最小官方调用
            try:
                _ocr_engine = PaddleOCR(
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                )
                logger.info("PaddleOCR 引擎初始化成功（兼容模式：关闭两项）")
            except Exception as e:
                logger.warning("PaddleOCR 实例初始化失败: %s: %s", type(e).__name__, e)
                _ocr_engine = None
        except Exception as e:
            logger.warning("PaddleOCR 实例初始化失败: %s: %s", type(e).__name__, e)
            _ocr_engine = None
        return _ocr_engine


def reset():
    """重置引擎状态（仅用于测试）。"""
    global _ocr_engine, _ocr_initialized
    with _ocr_lock:
        _ocr_engine = None
        _ocr_initialized = False
