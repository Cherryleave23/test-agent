"""独立重排器（reranker）抽象与开源实现。

设计原则：重排是与「双塔向量召回（bi-encoder retrieval）」**解耦的独立阶段**。
- 召回阶段（store.retrieve 的向量 + FTS 融合）负责「广度」：产出候选集。
- 重排阶段（本模块）负责「精度」：cross-encoder 对 (query, doc) 逐对打分重排。

这样做的好处：
1. 业务召回逻辑不受重排模型影响——重排器可插拔、可替换，不动检索代码；
2. mock / 轻量场景用 NoReranker 透传（不加载任何模型），真实场景再挂开源 cross-encoder；
3. 直接复用社区成熟方案（BAAI/bge-reranker-v2-m3 via FlagEmbedding），不重复造轮子。

内置：
- NoReranker：透传（分数置 1.0，调用方退回召回分数排序）。
- BgeReranker：基于 sentence-transformers.CrossEncoder 加载 BAAI/bge-reranker-v2-m3
  （开源多语言 cross-encoder，原生支持中文，母婴垂域精度显著优于静态权重启发式）。
"""
from __future__ import annotations

from typing import List, Protocol, runtime_checkable


@runtime_checkable
class Reranker(Protocol):
    kind: str

    def rerank(self, query: str, docs: List[str]) -> List[float]:
        """对候选文档逐对打分，返回与 docs 等长的相关性分数列表。"""
        ...


class NoReranker:
    """透传重排器：不加载模型，召回分数即最终分数（mock / 轻量场景）。"""

    kind = "none"

    def rerank(self, query: str, docs: List[str]) -> List[float]:
        return [1.0] * len(docs)


class BgeReranker:
    """开源 cross-encoder 重排：BAAI/bge-reranker-v2-m3。

    直接复用已安装的 sentence-transformers 的 CrossEncoder 加载（与本项目 bge 嵌入
    同源 transformers 后端，已验证可用），无需另装 FlagEmbedding，避免依赖冲突、
    也不重复造轮子。惰性加载 + 单例复用。

    可插拔设计：模型路径优先从插件管理器解析（本地插件目录），
    无则回退到 HuggingFace repo id（CrossEncoder 自动下载）。
    """

    kind = "bge-reranker-v2-m3"
    _model = None
    _name = "BAAI/bge-reranker-v2-m3"

    @classmethod
    def _resolve_model_name(cls) -> str:
        """通过插件管理器解析模型路径，实现可插拔。"""
        try:
            from common.plugins import PluginManager
            pm = PluginManager()
            resolved = pm.resolve_model("bge-reranker-v2-m3")
            if resolved:
                return resolved
        except Exception:
            pass
        return cls._name

    @classmethod
    def _get(cls):
        if cls._model is None:
            from sentence_transformers import CrossEncoder  # 惰性：仅真实重排路径才导入

            model_name_or_path = cls._resolve_model_name()
            cls._model = CrossEncoder(model_name_or_path)
        return cls._model

    def rerank(self, query: str, docs: List[str]) -> List[float]:
        model = self._get()
        pairs = [[query, d] for d in docs]
        # CrossEncoder.predict 对每对 (query, doc) 输出相关性分数，分数越高越相关
        scores = model.predict(pairs)
        return [float(s) for s in scores]


# 已注册的重排器实现（开箱即用的开源方案；新增方案在此登记即可）
_RERANKERS = {
    "none": NoReranker,
    "bge-reranker-v2-m3": BgeReranker,
}


def get_reranker(kind: str = "none") -> Reranker:
    """按 kind 获取重排器实例（单例由各实现自行维护）。"""
    cls = _RERANKERS.get(kind)
    if cls is None:
        raise NotImplementedError(f"reranker kind={kind} 尚未实现，可选：{list(_RERANKERS)}")
    return cls()
