# tbpu : text block processing unit 文本块后处理
#
# 来源: Umi-OCR (MIT License)
# 提取自 Umi-OCR 项目，移除了 umi_log 依赖（替换为标准 logging），
# 适配了相对导入路径。核心算法代码（GapTree、ParagraphParse）零修改。
#
# 提供专业级排版解析能力：
#   - GapTree 间隙树排序算法：自动识别多栏布局，按人类阅读顺序排序
#   - ParagraphParse 段落分析：预测行尾分隔符（换行/空格/无），恢复段落结构
#   - 8 种排版策略：多栏/单栏 × 自然段/单行/无换行 + 代码段


from .parser_none import ParserNone
from .parser_multi_para import MultiPara
from .parser_multi_line import MultiLine
from .parser_multi_none import MultiNone
from .parser_single_para import SinglePara
from .parser_single_line import SingleLine
from .parser_single_none import SingleNone
from .parser_single_code import SingleCode

# 排版解析策略注册表
Parser = {
    "none": ParserNone,  # 不做处理
    "multi_para": MultiPara,  # 多栏-自然段（默认，最适合产品图片）
    "multi_line": MultiLine,  # 多栏-总是换行
    "multi_none": MultiNone,  # 多栏-无换行
    "single_para": SinglePara,  # 单栏-自然段
    "single_line": SingleLine,  # 单栏-总是换行
    "single_none": SingleNone,  # 单栏-无换行
    "single_code": SingleCode,  # 单栏-代码段
}


def getParser(key):
    """获取排版解析器对象。key 不存在时返回 multi_para（默认策略）。"""
    if key in Parser:
        return Parser[key]()
    else:
        return Parser["multi_para"]()


# ===================== 数据格式适配层 =====================
# 我们的 OCR 结果格式（来自 _extract_lines）:
#   [(box, (text, score)), ...]
#   box = [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]  (numpy array 或 list)
#
# tbpu 期望的格式:
#   [{"box": [[x,y],[x,y],[x,y],[x,y]], "score": float, "text": str}, ...]
#
# 适配函数将我们的格式转换为 tbpu 格式，运行 tbpu，然后将结果拼接为文本。


def _to_tbpu_format(lines):
    """将我们的 OCR 行列表转换为 tbpu 字典格式。

    输入: [(box, (text, score)), ...]
    输出: [{"box": [[x,y]...], "score": float, "text": str}, ...]
    """
    blocks = []
    for box, (text, score) in lines:
        # box 可能是 numpy array，转为 list
        box_list = [[float(p[0]), float(p[1])] for p in box]
        blocks.append({
            "box": box_list,
            "score": float(score),
            "text": text,
        })
    return blocks


def _from_tbpu_format(blocks):
    """将 tbpu 处理后的结果拼接为最终文本。

    输入: [{"box":..., "score":..., "text":..., "end": str}, ...]
    输出: (text, low_conf)
    """
    parts = []
    low_conf = False
    for block in blocks:
        text = block.get("text", "")
        end = block.get("end", "\n")
        score = block.get("score", 0.0)
        if score < 0.5:
            low_conf = True
        parts.append(text + end)
    return "".join(parts).strip(), low_conf


def process_ocr_lines(lines, parser_key="multi_para"):
    """对 OCR 结果执行 tbpu 排版解析，返回拼接文本。

    参数:
        lines: 我们的 OCR 行列表 [(box, (text, score)), ...]
        parser_key: 排版策略，默认 "multi_para"（多栏-自然段）

    返回:
        (text, low_conf)
    """
    if not lines:
        return "", True

    # 转换为 tbpu 格式
    blocks = _to_tbpu_format(lines)

    # 获取解析器并执行
    parser = getParser(parser_key)
    blocks = parser.run(blocks)

    # 转换回文本
    return _from_tbpu_format(blocks)
