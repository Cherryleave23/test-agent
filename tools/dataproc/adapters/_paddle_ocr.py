"""共享 PaddleOCR 引擎单例：线程安全双重检查锁定。

为 pdf.py 和 image_table.py 提供统一的 PaddleOCR 引擎获取入口，
避免重复初始化与模块级状态的线程安全问题（P1-N3 / P2-N3）。
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# 模块级单例：避免每次调用重新初始化 PaddleOCR 引擎
_ocr_engine = None
_ocr_initialized = False
_ocr_lock = threading.Lock()


def get_paddle_ocr():
    """获取 PaddleOCR 引擎单例（线程安全，双重检查锁定）。

    首次调用初始化，后续复用。缺 paddleocr 时返回 None 并记 info 日志；
    初始化失败时返回 None 并记 warning 日志。
    """
    global _ocr_engine, _ocr_initialized
    # 快速路径：已初始化直接返回（无锁）
    if _ocr_initialized:
        return _ocr_engine
    with _ocr_lock:
        # 双重检查：拿到锁后再次确认，避免重复初始化
        if _ocr_initialized:
            return _ocr_engine
        _ocr_initialized = True
        # 缺 paddleocr：返回 None，记 info 日志
        try:
            from paddleocr import PaddleOCR
        except ImportError:
            logger.info("paddleocr 未安装，PaddleOCR 引擎不可用")
            _ocr_engine = None
            return _ocr_engine
        # 初始化失败：返回 None，记 warning 日志
        try:
            _ocr_engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
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
