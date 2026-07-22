"""共享 PaddleOCR 引擎单例：线程安全双重检查锁定。

PaddleOCR 3.7 + PP-OCRv6_small 配置：

模型选择（PP-OCRv6，PaddleOCR 3.7 默认）：
  - PP-OCRv6_small_det: 检测 Hmean 84.1%，9.6MB
  - PP-OCRv6_small_rec: 识别精度 81.3%，20.4MB
  - 单模型支持中、英、日及 46 种拉丁语系共 50 种语言

推理引擎配置（官方 engine_config API）：
  - engine="paddle_static" + run_mode="mkldnn" — oneDNN CPU 加速
  - 官方 enable_new_ir=False / delete_pass 在 Windows 上无法绕过 PIR bug，
    需要 monkey-patch set_optimization_level=0
  - 参考: https://www.paddleocr.ai/latest/version3.x/inference_deployment/local_inference/inference_engine.html

性能基准（5712x4284 产品图片）：
  v6_small + 文件路径 + mkldnn: ~10s, 110 chars（当前配置）
  v6_medium + 文件路径 + mkldnn: ~50s, 112 chars（精度略高但太慢）
  v5_mobile + 文件路径 + mkldnn: ~8s, 103 chars（精度略低）

注意：必须传文件路径给 predict()，不能预缩放图片（预缩放到 1600px 会导致精度暴跌）。
PaddleOCR 3.x 内置 max_side_limit=4000 的自动缩放。
"""
from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

# 在导入 paddle 前设置环境变量（辅助 PIR 禁用）
os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")
os.environ.setdefault("FLAGS_pir_apply_inplace_pass", "0")


def _patch_paddle_inference_config():
    """Monkey-patch paddle.inference.Config 降低 PIR 优化级别。

    PaddlePaddle 3.x 在 Windows 上 mkldnn + PIR executor 组合有 bug:
      NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support

    官方 engine_config 中 enable_new_ir=False 和 delete_pass=["pir_optimize_pass"]
    均无法绕过此问题（实测验证），只有将 set_optimization_level 强制设为 0 才有效。
    此 patch 保留 mkldnn 加速同时绕过 PIR 指令问题。

    参考: https://www.paddleocr.ai/latest/version3.x/inference_deployment/local_inference/inference_engine.html#paddle_static
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

    配置：PP-OCRv6_small + mkldnn + patch
    单张图片 OCR 约 10 秒（传文件路径，PaddleOCR 自动缩放到 4000px）。
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
                # PP-OCRv6_small：PaddleOCR 3.7 最新模型，兼顾精度与速度
                text_detection_model_name="PP-OCRv6_small_det",
                text_recognition_model_name="PP-OCRv6_small_rec",
                # 关闭不需要的模块（产品图片不需要方向分类/矫正）
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
            )
            logger.info(
                "PaddleOCR 3.x 引擎初始化成功 "
                "(PP-OCRv6_small + mkldnn, 方向分类=OFF)"
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
