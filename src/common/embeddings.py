"""嵌入模型封装：默认 mock（确定性、无外部依赖），可切换 bge-small-zh 本地真实语义嵌入。

- mock：词袋 + 中文字 bigram 的确定性向量（无外部服务，便于 harness 复现）。
- bge：BAAI/bge-small-zh-v1.5（中文语义 SOTA 小模型，512 维，端侧 CPU 可跑），
  通过 sentence-transformers 惰性加载，向量 L2 归一化。

`embed()` 按 model_kind 调度；不同模型维度不同（mock=384 / bge=512），由 EMBED_DIM 查询。
Chroma 按集合自适应维度，SQLite FTS5 与向量维度无关。
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import List, Optional

DIM = 262144  # mock 维度：足够大以消解哈希碰撞噪声，使零重叠真正趋近正交（L2≈√2），
              # 从而与在域弱相关查询（L2≈1.39）在阈值处可分离。

# 各嵌入模型的输出维度（Chroma 按集合自适应；查询需与入库同维）。
EMBED_DIM = {
    "mock": 262144,
    "bge": 512,
    "bge-small-zh": 512,
    "bge-small-zh-v1.5": 512,
}


def embed_dim(model_kind: str = "mock") -> int:
    for k, d in EMBED_DIM.items():
        if model_kind.startswith(k):
            return d
    return DIM


# 母婴核心品类词：作为整体 token（不被拆成单字），让"奶粉/营养品/尿不湿"等查询
# 与产品产生明显整体相似，避免单字切分导致的弱匹配。
CATEGORY_TERMS = [
    "奶粉", "牛奶粉", "羊奶粉", "特配奶粉", "营养品", "益生菌", "DHA", "钙", "维生素",
    "尿不湿", "纸尿裤", "婴儿", "婴幼儿", "宝宝", "孕妇", "段位", "配方", "opo", "a2",
]


def _category_terms(text: str) -> List[str]:
    out: List[str] = []
    low = text.lower()
    for term in CATEGORY_TERMS:
        if term.lower() in low:
            out.append(term.lower())
    return out


# 母婴领域复合词表：除 CATEGORY_TERMS 外的常用多字词，用于「精确分词」。
# 原则：只匹配领域词，不做单字 / bigram 重叠 —— 这样 OOV 跨域文本（如「汽车轮胎」）
# 不会产生任何 token → 向量退化为固定正交哨兵、FTS 无命中 → 被门控干净丢弃，
# 从根上杜绝「卖」等通用字在跨域查询与「卖点」块之间的误匹配。
MATERNITY_LEXICON = [
    "益生元", "牛磺酸", "胆碱", "叶黄素", "蛋白质", "脂肪", "碳水化合物", "膳食纤维",
    "生牛乳", "生鲜乳", "奶基", "配料", "成分", "月龄", "吸收", "免疫", "肠道",
    "过敏", "早产", "全营养", "低出生体重", "氨基酸", "特配", "注册号", "适用",
    "幼儿", "脑发育", "视力", "智力", "便秘", "腹泻", "抵抗力", "抗感染",
    "护眼", "助眠",
]


def _domain_tokens(text: str) -> List[str]:
    """领域分词：英文 / 数字词 + 命中的母婴复合词（CATEGORY_TERMS ∪ MATERNITY_LEXICON）。

    不做单字切分。OOV 文本（无任何领域词）返回空列表 —— 上层据此判定跨域。
    """
    low = text.lower()
    toks: set[str] = set(_category_terms(text))
    for w in MATERNITY_LEXICON:
        if w.lower() in low:
            toks.add(w.lower())
    for m in re.findall(r"[a-zA-Z0-9]+", low):
        toks.add(m)
    return list(toks)


# 词汇维度保护区：token 哈希落在 [0, DIM-RESERVE)，哨兵维度在保护区外，
# 保证 OOV 哨兵向量与一切领域向量维度不交 → 严格正交（L2≈√2）。
RESERVE = 1024
NULL_IDX = DIM - 1


def _null_vector(dim: int = DIM) -> List[float]:
    """OOV 哨兵向量：仅置于保留维度，与所有领域向量严格正交。"""
    v = [0.0] * dim
    v[NULL_IDX] = 1.0
    return v


def mock_embed(text: str, dim: int = DIM) -> List[float]:
    """确定性 mock 嵌入：领域词袋（TF），L2 归一化。

    仅对母婴领域 token 建向量；跨域 OOV 文本无领域 token → 返回固定正交哨兵向量
    （L2≈√2≈1.414 > 门控阈值 1.405）→ 被相关性门控丢弃，从根上防跨域幻觉。
    在域文本因共享领域词袋 L2 明显更小（<1.4），得以保留。
    """
    grams = _domain_tokens(text)
    if not grams:
        return _null_vector(dim)
    vec = [0.0] * dim
    for g in grams:
        h = int.from_bytes(hashlib.md5(g.encode("utf-8")).digest()[:8], "big")
        idx = h % (dim - RESERVE)
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


class _BgeEmbedder:
    """bge-small-zh-v1.5 惰性单例封装（避免无谓加载大模型）。

    可插拔设计：模型路径优先从插件管理器解析（本地插件目录），
    无则回退到 HuggingFace repo id（sentence-transformers 自动下载）。
    """

    _model = None
    _name = "BAAI/bge-small-zh-v1.5"

    @classmethod
    def _resolve_model_name(cls) -> str:
        """通过插件管理器解析模型路径，实现可插拔。

        优先级：已安装的本地插件路径 > HuggingFace repo id
        """
        try:
            from common.plugins import PluginManager
            pm = PluginManager()
            resolved = pm.resolve_model("bge-small-zh-v1.5")
            if resolved:
                return resolved
        except Exception:
            pass
        return cls._name

    @classmethod
    def _get(cls):
        if cls._model is None:
            from sentence_transformers import SentenceTransformer  # 惰性：仅真实嵌入路径才导入

            model_name_or_path = cls._resolve_model_name()
            cls._model = SentenceTransformer(model_name_or_path)
        return cls._model

    @classmethod
    def encode(cls, text: str) -> List[float]:
        model = cls._get()
        vec = model.encode([text], normalize_embeddings=True, convert_to_numpy=True)
        return [float(x) for x in vec[0]]


def _is_bge(model_kind: str) -> bool:
    return model_kind.startswith("bge")


def embed(text: str, model_kind: str = "mock", dim: int = DIM, **kw) -> List[float]:
    """统一嵌入入口。

    model_kind:
      - mock / default          确定性词袋（无外部依赖，harness 默认）
      - bge / bge-small-zh / ... BAAI/bge-small-zh-v1.5 真实语义嵌入（端侧）
    """
    if model_kind in ("mock", "default"):
        return mock_embed(text, dim)
    if _is_bge(model_kind):
        return _BgeEmbedder.encode(text)
    raise NotImplementedError(f"embedding kind={model_kind} 尚未实现")
