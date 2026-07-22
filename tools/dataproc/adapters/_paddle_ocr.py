"""共享 PaddleOCR 引擎单例：线程安全双重检查锁定。

PaddleOCR 3.x 兼容性修复：
1. 在导入 paddle 之前设置环境变量禁用 PIR 和 oneDNN
2. monkey-patch paddle.inference.Config 强制禁用 new_ir/new_executor
   （PaddleX runner 在 CPU 模式下强制启用，导致 Windows NotImplementedError）
3. 使用 predict() 代替 ocr(cls=True)（3.x API 变更）
"""
from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

# === 关键：在导入 paddle/paddleocr 之前设置环境变量 ===
os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")
os.environ.setdefault("FLAGS_pir_apply_inplace_pass", "0")
os.environ.setdefault("FLAGS_use_mkldnn", "0")


def _patch_paddle_inference_config():
    """Monkey-patch paddle.inference.Config 降低 PIR 优化级别。

    PaddleX 的 PaddleStaticRunner 在 CPU 模式下会调用:
      config.enable_new_ir(True)
      config.enable_new_executor()
      config.set_optimization_level(3)  ← 级别3触发 oneDNN PIR 指令
    导致 Windows 上:
      NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support

    修复：patch set_optimization_level 强制设为 0（禁用激进优化），
    保留 new_ir/new_executor 正常工作。
    """
    try:
        import paddle.inference as paddle_inference

        _orig_set_opt = paddle_inference.Config.set_optimization_level

        def _patched_set_opt(self, level=3):
            # 强制优化级别为 0，避免 oneDNN PIR 指令
            return _orig_set_opt(self, 0)

        paddle_inference.Config.set_optimization_level = _patched_set_opt
        logger.info("Paddle inference Config patched: optimization_level=0")
    except Exception as e:
        logger.warning("Failed to patch paddle.inference.Config: %s: %s", type(e).__name__, e)


# 模块级单例
_ocr_engine = None
_ocr_initialized = False
_ocr_lock = threading.Lock()


def get_paddle_ocr():
    """获取 PaddleOCR 引擎单例（线程安全，双重检查锁定）。"""
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

        # 在创建 PaddleOCR 实例前 patch inference config
        _patch_paddle_inference_config()

        try:
            _ocr_engine = PaddleOCR(lang="ch")
        except TypeError:
            try:
                _ocr_engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
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
