"""图片/规格表/电商长图适配器：PaddleOCR 3.x（官方 PP-OCRv6_medium）+ tbpu 排版解析 + 长图切片 + 超大图处理。

遵循官方运行方式（精度优先，不预缩放、不做精度折衷的 hack）：
  - 正常图片（max(宽,高) ≤ 4000）：传文件路径给 predict()，PaddleOCR 3.x 内置 max_side_limit=4000 自动缩放。
  - 超大图（任一边长 > 4000，且非"瘦长条"）：采用社区最佳实践"缩小检测 + 原图识别"——
    在缩放图（长边 ≤4000，保证坐标映射精确）上做文本检测定位，坐标映射回原图后从【原图】
    裁剪高清小图做识别。识别在原图分辨率上进行，避免官方把整图缩放到 4000 后识别丢失细节
    （精度优先；参考用户提供的方案，已适配 PaddleOCR 3.x API，用官方 predict() 而非 2.x ocr()）。
  - 长图（高>3×宽）：按原始分辨率纵向切片（每片 1200px，带重叠避免切断行）后逐片 OCR。
    切片与原图识别同理都是"保分辨率"，精度中立/改善，与官方方案不冲突。

排版解析：使用 Umi-OCR 的 tbpu 模块（GapTree 间隙树 + ParagraphParse 段落分析），
自动识别多栏布局并按人类阅读顺序排序，替代原来的简单 (min_y, min_x) 排序。

零 src.*。无文字/低置信标 low_conf，绝不编造。"""
from __future__ import annotations

import logging

import numpy as np
from PIL import Image

from . import OCRDeferred, OCRDependencyMissing, AdapterResult, paddle_available
from ._paddle_ocr import get_paddle_ocr
from .tbpu import process_ocr_lines

logger = logging.getLogger(__name__)

# 长图纵向切片阈值：高 > SLICE_RATIO*宽 视为长图
SLICE_RATIO = 3.0
SLICE_H = 1200          # 单切片像素高
SLICE_OVERLAP = 120     # 切片重叠，避免切断行

# 超大图（任一边长超过模型 max_side_limit=4000）处理参数
# 方案：缩小图做检测（定位文字框）→ 坐标映射回原图 → 原图高清裁剪做识别。
# 这是 PaddleOCR 社区公认的"大图 OCR 最佳实践"，精度优先（识别在原图分辨率上进行，
# 避免官方把整图缩放到 4000 后识别丢失细节）。仅对超出限制的大图启用，普通图仍走官方路径。
LARGE_IMAGE_LIMIT = 4000   # 触发阈值：max(宽,高) > 此值视为超大图（走方案一二合一）
LARGE_DET_SHORT_SIZE = 1500  # 检测用缩图的目标短边（越小检测越快，但过小会漏检小字）
LARGE_DET_MAX_SIDE = 4000   # 检测用缩图的长边上限（=模型 max_side_limit，保证不触发模型内部再缩放，坐标映射才精确）
LARGE_CROP_PADDING = 5      # 原图裁剪外扩边距，防止切太紧


def _slice_long_rgb(arr: np.ndarray):
    """长图（高>宽数倍）纵向切片，带重叠避免切断。RGB 输入/输出。"""
    h, w = arr.shape[:2]
    if h <= SLICE_RATIO * w:
        yield arr
        return
    step = SLICE_H - SLICE_OVERLAP
    last_y = 0
    for y in range(0, max(h - SLICE_H, 0) + 1, step):
        yield arr[y:y + SLICE_H]
        last_y = y
    # 末片收尾：仅当还存在未被覆盖的尾部时补一片
    if h - SLICE_H > last_y:
        yield arr[h - SLICE_H:h]


def _extract_polys(res):
    """从 PaddleOCR 3.x predict 结果中提取检测多边形（4×2，numpy）。

    输入 res: list[OCRResult]（list(ocr.predict(...)) 的返回）
    返回: [np.ndarray(shape=(4,2)), ...]
    """
    if not res:
        return []
    first = res[0]
    if isinstance(first, dict) and "dt_polys" in first:
        return [np.array(p, dtype=np.float64) for p in first.get("dt_polys", [])]
    return []


def _detect_recognize_scaled(ocr, tile: np.ndarray, original: np.ndarray, off_x=0, off_y=0):
    """缩小检测 + 原图识别（核心，方案一二合一的基础单元）。

    在 tile（整图或切片）上缩小做检测，定位文字框；坐标映射回【original 原图】的全局坐标系，
    从 original 裁剪高清小图做识别——识别始终在原图分辨率上进行，保精度。
    off_x/off_y 为该 tile 在 original 中的左上角偏移（切片兜底时为非零）。

    返回 [(global_box(4,2,ndarray), (text, score)), ...]（global_box 为 original 坐标系）。
    """
    h, w = tile.shape[:2]
    oh, ow = original.shape[:2]
    short, long = min(h, w), max(h, w)

    # 1) 检测用缩图（长边 ≤ max_side_limit，短边尽量接近目标；绝不放大）
    scale = min(LARGE_DET_SHORT_SIZE / short, LARGE_DET_MAX_SIDE / long, 1.0)
    if scale < 1.0:
        det_w, det_h = int(round(w * scale)), int(round(h * scale))
        det_img = np.array(Image.fromarray(tile).resize((det_w, det_h)))
    else:
        det_img = tile

    # 2) 缩图检测（det_img 长边已 ≤ LARGE_DET_MAX_SIDE=4000=模型默认 max_side_limit，
    #    模型不会在内部再缩放，坐标映射才精确；故不额外传 text_det_limit_side_len）
    det_res = list(ocr.predict(det_img))
    polys = _extract_polys(det_res)
    if not polys:
        return []

    # 3) 坐标映射回 original 全局坐标 → 从 original 裁剪 → 识别
    sx, sy = w / det_img.shape[1], h / det_img.shape[0]
    crops, boxes = [], []
    for poly in polys:
        local = (poly * [sx, sy]).astype(np.int32)          # tile 内坐标
        x1, y1 = local.min(axis=0)
        x2, y2 = local.max(axis=0)
        x1 = max(0, x1 - LARGE_CROP_PADDING)
        y1 = max(0, y1 - LARGE_CROP_PADDING)
        x2 = min(w, x2 + LARGE_CROP_PADDING)
        y2 = min(h, y2 + LARGE_CROP_PADDING)
        # 映射到 original 全局坐标并夹紧
        gx1, gx2 = max(0, x1 + off_x), min(ow, x2 + off_x)
        gy1, gy2 = max(0, y1 + off_y), min(oh, y2 + off_y)
        crop = original[gy1:gy2, gx1:gx2]
        if crop.size == 0:
            continue
        crops.append(crop)
        gbox = local.copy()
        gbox[:, 0] += off_x
        gbox[:, 1] += off_y
        boxes.append(gbox)

    if not crops:
        return []

    # 4) 原图裁剪批量识别
    rec_results = list(ocr.predict(crops))
    lines = []
    for box, rr in zip(boxes, rec_results):
        if not isinstance(rr, dict):
            continue
        texts = rr.get("rec_texts", []) or []
        scores = rr.get("rec_scores", []) or []
        if texts:
            text = "".join(str(t) for t in texts)
            score = float(min(scores)) if scores else 0.0
            lines.append((box, (text, score)))

    return lines


def _ocr_large_image(ocr, arr: np.ndarray):
    """普通超大图（max(边) > 4000）：缩小检测 + 原图识别（精度优先，方案一二合一）。

    这是大图 OCR 的统一处理入口。关于方案二（2.x 的 slice 切片）：
    PaddleOCR 3.x 的 predict() **没有 slice 参数**（已核实 3.7.0 全包无 slice/horizontal_stride
    定义）——2.x 需要切片是因为无法高效处理超大图的整图检测；而本方案在缩图上以模型原生
    max_side_limit(=4000) 做**单次整图检测**（检测分辨率已是 3.x 最优），再在原图分辨率上识别，
    已在精度与速度上覆盖方案二的诉求，故 3.x 下无需再手写切片。若遇超大图中"文字极小"导致
    4000px 检测仍漏检，应调高 LARGE_DET_MAX_SIDE（并同步放开模型 text_det_limit_side_len），
    而非滑窗切片。
    """
    lines = _detect_recognize_scaled(ocr, arr, arr, 0, 0)
    if not lines:
        return "", True
    return process_ocr_lines(lines, "multi_para")


def _ocr_predict(ocr, input_data):
    """调用 PaddleOCR predict()，兼容文件路径和 numpy 数组输入。

    3.x: ocr.predict(input) → list[OCRResult]
    2.x: ocr.ocr(input, cls=True)
    """
    try:
        return list(ocr.predict(input_data))
    except (TypeError, AttributeError):
        try:
            return ocr.ocr(input_data, cls=True)
        except TypeError:
            return ocr.ocr(input_data)


def _extract_lines(res):
    """从 PaddleOCR 结果中提取行列表（兼容 2.x 和 3.x 格式）。

    3.x: res = [OCRResult(dict子类)], OCRResult 有 rec_texts/rec_scores/dt_polys
    2.x: res = [[[box, (txt, score)], ...]]

    返回: [(box, (text, score)), ...]
    """
    if not res:
        return []
    first = res[0]

    # 3.x 格式：OCRResult 是 dict 子类，有 rec_texts 键
    if isinstance(first, dict) and "rec_texts" in first:
        d = first
        texts = d.get("rec_texts", [])
        scores = d.get("rec_scores", [])
        polys = d.get("dt_polys", [])
        lines = []
        for i in range(len(texts)):
            txt = texts[i] if i < len(texts) else ""
            score = float(scores[i]) if i < len(scores) else 0.0
            box = polys[i] if i < len(polys) else [[0, 0], [0, 0], [0, 0], [0, 0]]
            lines.append((box, (txt, score)))
        return lines

    # 2.x 格式：res[0] 是列表
    if isinstance(first, list):
        return first[0] if first and isinstance(first[0], list) else first

    return []


def _extract_text_with_tbpu(results, parser_key="multi_para"):
    """从 OCR 结果中提取文本，使用 tbpu 排版解析。

    1. 提取 (box, (text, score)) 行列表
    2. 交给 tbpu process_ocr_lines 进行多栏排序 + 段落分析
    3. 返回 (text, low_conf)
    """
    if not results:
        return "", True

    lines = _extract_lines(results)
    if not lines:
        return "", True

    return process_ocr_lines(lines, parser_key)


class ImageTableAdapter:
    """图片/规格表/长图适配器。

    正常图片传文件路径给 PaddleOCR（内置 4000px 自动缩放，不做预缩放）；
    长图按原始分辨率切片后逐片 OCR。
    OCR 结果统一走 tbpu 排版解析（多栏-自然段策略）。
    """
    kind = "image_table"

    def extract(self, path: str, run_real_ocr: bool = False) -> AdapterResult:
        from PIL import Image
        try:
            if not run_real_ocr:
                raise OCRDeferred("run_real_ocr=False，图片 OCR 推迟")
            if not paddle_available():
                raise OCRDependencyMissing(
                    "PaddleOCR 未安装，无法对图片做 OCR（RUN_REAL_OCR=1 但缺依赖）")

            ocr = get_paddle_ocr()
            if ocr is None:
                raise OCRDependencyMissing(
                    "PaddleOCR 未安装，无法对图片做 OCR（RUN_REAL_OCR=1 但缺依赖）")

            # 读取图片尺寸判断分支：超大图 / 长图 / 正常图
            with Image.open(path) as pil:
                w, h = pil.size
                arr = np.array(pil.convert("RGB"))
            is_long = h > SLICE_RATIO * w
            is_oversized = max(h, w) > LARGE_IMAGE_LIMIT

            if is_long:
                # 长图（高>3×宽）：按原始分辨率纵向切片后逐片 OCR（保分辨率）
                text_parts: list = []
                low_conf = False
                for chunk in _slice_long_rgb(arr):
                    results = _ocr_predict(ocr, chunk)
                    t, lc = _extract_text_with_tbpu(results)
                    if t:
                        text_parts.append(t)
                    low_conf = low_conf or lc
                text = "\n".join(text_parts).strip()
                if not text:
                    low_conf = True
            elif is_oversized:
                # 超大图（任一边长 > 4000，且非"瘦长条"）：缩小检测 + 原图识别（方案一二合一，精度优先）
                text, low_conf = _ocr_large_image(ocr, arr)
            else:
                # 正常图片：传文件路径，PaddleOCR 3.x 内置 max_side_limit=4000 自动缩放
                results = _ocr_predict(ocr, path)
                text, low_conf = _extract_text_with_tbpu(results)

        except (OCRDeferred, OCRDependencyMissing):
            raise
        except Exception as e:
            logger.exception("图片适配器处理失败 %s: %s", type(e).__name__, e)
            raise RuntimeError(f"图片处理失败: {type(e).__name__}: {e}") from e

        meta = {"source": "image", "ocr": True, "low_conf": low_conf}
        return AdapterResult(
            text=text,
            meta=meta,
            tables=[],
            low_conf=low_conf,
        )
