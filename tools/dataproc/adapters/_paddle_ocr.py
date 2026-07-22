"""共享 PaddleOCR 引擎单例：线程安全双重检查锁定。

PaddleOCR 3.x 性能优化配置：
  1. mkldnn (oneDNN) 加速 — CPU 推理比 paddle 快 5-10x
  2. monkey-patch set_optimization_level=0 — 绕过 Windows PIR/oneDNN bug
  3. PP-OCRv5 mobile 模型 — 比 server 快 3x，精度损失极小
  4. 关闭文档方向分类/矫正 — 产品图片不需要，省 2 个模型加载

性能基准（4284x5712 图片，预缩放到 1600px）：
  server + paddle:  56s
  server + mkldnn:  12s
  mobile + mkldnn:   4.5s  ← 当前配置
"""
from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

# 在导入 paddle 前设置环境变量
os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")
os.environ.setdefault("FLAGS_pir_apply_inplace_pass", "0")


def _patch_paddle_inference_config():
    """Monkey-patch paddle.inference.Config 降低 PIR 优化级别。

    PaddlePaddle 3.x 在 Windows 上 mkldnn + PIR executor 组合有 bug:
      NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support
    将 set_optimization_level 强制设为 0 可绕过此问题，同时保留 mkldnn 加速。

    参考: https://www.paddleocr.ai/latest/version3.x/inference_deployment/local_inference/inference_engine.html
    """
    try:
        import paddle.inference as paddle_inference

        _orig_set_opt = paddle_inference.Config.set_optimization_level

        def _patched_set_opt(self, level=3):
            return _orig_set_opt(self, 0)

        paddle_inference.Config.set_optimization_level = _patched_set_opt
        logger.info("Paddle inference Config patched: optimization_level=0 (mkldnn compatible)")
    except Exception as e:
        logger.warning("Failed to patch paddle.inference.Config: %s: %s", type(e).__name__, e)


# 模块级单例
_ocr_engine = None
_ocr_initialized = False
_ocr_lock = threading.Lock()


def get_paddle_ocr():
    """获取 PaddleOCR 引擎单例（线程安全，双重检查锁定）。

    配置：mkldnn + mobile 模型 + 关闭方向分类
    单张图片 OCR 约 3-5 秒（预缩放到 1600px 后）。
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

        # mkldnn 需要 patch 才能在 Windows 上工作
        _patch_paddle_inference_config()

        try:
            _ocr_engine = PaddleOCR(
                lang="ch",
                engine="paddle_static",
                engine_config={
                    "device_type": "cpu",
                    "cpu_threads": 4,
                    "run_mode": "mkldnn",
                },
                # mobile 模型：比 server 快 3x，精度损失极小
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_mobile_rec",
                # 关闭不需要的模块（产品图片不需要方向分类/矫正）
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
            )
            logger.info(
                "PaddleOCR 3.x 引擎初始化成功 "
                "(mkldnn + mobile models, 方向分类=OFF)"
            )
        except TypeError:
            # 兼容：如果参数不支持，退回基础构造
            try:
                _ocr_engine = PaddleOCR(lang="ch")
                logger.info("PaddleOCR 引擎初始化成功（兼容模式）")
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
