# tbpu : text block processing unit
# 文块处理器的基类。
# 来源: Umi-OCR (MIT License), 原样提取。


class Tbpu:
    def __init__(self):
        self.tbpuName = "文块处理单元-未知"

    def run(self, textBlocks):
        """输入：textBlocks文块列表。例：\n
        [
            {'box': [[29, 19], [172, 19], [172, 44], [29, 44]], 'score': 0.89, 'text': '文本111'},
            {'box': [[29, 60], [161, 60], [161, 86], [29, 86]], 'score': 0.75, 'text': '文本222'},
        ]
        输出：排序后的textBlocks文块列表，每个块增加键：
        'end' 结尾间隔符
        """
        return textBlocks
