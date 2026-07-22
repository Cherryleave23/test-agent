"""共享 PaddleOCR 引擎单例：线程安全双重检查锁定。

PaddleOCR 3.x 正式 API：
  - PaddleOCR(lang="ch", engine="paddle_static", engine_config={...})
  - ocr.predict(image) → list[OCRResult]
  - OCRResult 是 dict 子类：rec_texts / rec_scores / dt_polys

Windows PIR/oneDNN 兼容：
  官方 engine_config 中 run_mode="paddle"（非 "mkldnn"）可绕过
  ConvertPirAttribute2RuntimeAttribute NotImplementedError。
  参考: https://www.paddleocr.ai/latest/version3.x/inference_deployment/local_inference/inference_engine.html
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# 模块级单例
_ocr_engine = None
_ocr_initialized = False
_ocr_lock = threading.Lock()


def get_paddle_ocr():
    """获取 PaddleOCR 引擎单例（线程安全，双重检查锁定）。

    使用 PaddleOCR 3.x 官方 API：
      engine="paddle_static" + engine_config={"run_mode": "paddle"}
    避免 Windows oneDNN PIR 指令 NotImplementedError。
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

        try:
            _ocr_engine = PaddleOCR(
                lang="ch",
                engine="paddle_static",
                engine_config={
                    "device_type": "cpu",
                    "cpu_threads": 4,
                    "run_mode": "paddle",  # 非 mkldnn，绕过 Windows PIR 问题
                },
            )
            logger.info("PaddleOCR 3.x 引擎初始化成功 (engine=paddle_static, run_mode=paddle)")
        except TypeError:
            # 兼容：如果 engine/engine_config 不被支持（旧版），退回基础构造
            try:
                _ocr_engine = PaddleOCR(lang="ch")
                logger.info("PaddleOCR 引擎初始化成功（兼容模式，无 engine_config）")
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
