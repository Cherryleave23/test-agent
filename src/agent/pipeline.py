"""RAG 问答管线（MOD-agent，6 步，G3）。

步骤：
1) 检索：KB 混合检索（HQ 知识库 + 本企业 B-end 产品）
2) 拼接上下文：把命中组织为可引用上下文
3) 企业 prompt：system + 企业定制 + 母婴免责
4) 调用 LLM：ProviderFactory 选中的 provider
5) 引用：把命中作为引用附回（可追溯、防幻觉）
6) 防幻觉/免责：无上下文不编造；末尾附母婴健康免责
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Optional

from common.config import EnterpriseConfig
from kb.store import KnowledgeStore, CorpusHit
from agent.providers import ProviderFactory
from baby.models import BabyProfile


DISCLAIMER = "（温馨提示：以上为产品信息参考，不构成医疗诊断，如有健康问题请咨询专业医生。）"

# F3 意图→内容类型路由：根据问题语义把查询映射到知识 kind，使检索按内容类型加权/路由。
# 设计：轻量关键词分类（确定性、无 LLM 依赖、可测试），后续可替换为 LLM 抽类型。
# 三类知识：product_text（产品卖点/配方）、article（育儿科普）、ingredient（成分/营养）。
_INTENT_KEYWORDS = {
    "ingredient": ["成分", "营养", "含量", "配料", "DHA", "钙", "铁", "锌", "益生菌",
                   "蛋白质", "脂肪", "碳水", "opo", "a2", "过敏", "乳糖", "维生素",
                   "膳食纤维", "叶黄素", "牛磺酸", "胆碱", "益生元"],
    "product_text": ["卖点", "配方", "段位", "品牌", "价格", "注册号", "国食注字",
                     "推荐", "主打", "产品", "系列", "规格", "包装"],
    "article": ["辅食", "喂养", "睡眠", "育儿", "月龄", "早教", "护理", "注意事项",
                "怎么喂", "如何", "为什么", "什么时候", "禁忌", "护理"],
}


def classify_intent(query: str) -> Optional[str]:
    """把用户问题归类为知识 kind（product_text/article/ingredient），无明确意图返回 None。

    计数命中取最高频类型；平局时优先级 ingredient > product_text > article
    （成分问题答错代价最高，优先保成分召回）。
    """
    low = (query or "").lower()
    scores = {k: 0 for k in _INTENT_KEYWORDS}
    for kind, kws in _INTENT_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in low:
                scores[kind] += 1
    best = max(scores.values())
    if best == 0:
        return None
    # 取命中数最高者；并列时按优先级
    order = ["ingredient", "product_text", "article"]
    for kind in order:
        if scores[kind] == best:
            return kind
    return None


def intent_kind_weight(query: str) -> Optional[dict]:
    """F3 加权：检测到明确意图时，对该 kind 乘性提权（1.8），其余保持 1.0。

    仅加权、不剔除——避免意图误判导致相关结果被排除，召回更稳健。
    """
    kind = classify_intent(query)
    if kind is None:
        return None
    return {kind: 1.8}


@dataclass
class Answer:
    text: str
    citations: List[Dict] = field(default_factory=list)
    hits: List[CorpusHit] = field(default_factory=list)
    empty: bool = False  # 无检索命中（防幻觉标记）


class Agent:
    def __init__(self, cfg: EnterpriseConfig, store: KnowledgeStore):
        self.cfg = cfg
        self.store = store
        self.provider = ProviderFactory.get(cfg.llm)

    def _build_context(self, hits: List[CorpusHit]) -> str:
        if not hits:
            return "（无相关检索结果）"
        lines = []
        for i, h in enumerate(hits, 1):
            meta = h.meta
            label = meta.get("name") or h.title
            chunk = f"【{h.chunk}】" if h.chunk else ""
            lines.append(f"[引用{i}] {label}{chunk}\n{h.content}")
        return "\n\n".join(lines)

    def _build_messages(self, query: str, context: str,
                        history: List[Dict[str, str]],
                        constraints=None,
                        baby_block: Optional[str] = None) -> List[Dict[str, str]]:
        # Prompt Caching（优化 C·阶段2）：稳定内容在前，动态内容在后，使前缀命中缓存。
        # 稳定层：企业 system prompt + 指令（跨所有查询不变，必命中）
        stable_text = (
            f"{self.cfg.system_prompt}\n"
            "仅依据下方【企业知识库】内容回答，不得编造未提及的信息；"
            "如知识库无相关内容，明确告知用户暂无信息。"
        )
        # 半稳定层：宝宝档案块 + 约束块（会话内大部分时间不变，会话内命中）
        semi_stable_parts: List[str] = []
        if baby_block:
            semi_stable_parts.append(baby_block)
        # P1 约束块注入（方向 B 累积 + 方向 A 压缩的产物）：可选，空则不注入
        if constraints is not None:
            block = constraints.to_prompt_block()
            if block:
                semi_stable_parts.append(block)
        # 动态层：检索上下文（每轮变化，不进缓存前缀）
        dynamic_text = f"【企业知识库】\n{context}"

        system_content = stable_text
        if semi_stable_parts:
            system_content += "\n\n" + "\n\n".join(semi_stable_parts)
        system_content += "\n\n" + dynamic_text

        msgs: List[Dict[str, str]] = [{"role": "system", "content": system_content}]
        for h in history:
            msgs.append({"role": h["role"], "content": h["content"]})
        msgs.append({"role": "user", "content": query})
        return msgs

    def _enrich_query(self, query: str, baby_profile: Optional[BabyProfile] = None) -> str:
        """用宝宝档案上下文增强检索查询，提升 KB 命中率（Fix#1）。

        原始查询（如"该吃什么辅食"）过于泛化，KB 中"14个月辅食""早产宝宝辅食"等内容
        会因查询不含这些关键词而漏检。本方法把焦点宝宝的月龄/段位/过敏/品牌/品类/病史
        等拼入查询，使检索能命中更精准的文档。
        """
        if baby_profile is None:
            return query
        parts = [query]
        bp = baby_profile
        if bp.baby_age:
            parts.append(bp.baby_age)
        if bp.stage:
            parts.append(bp.stage)
        if bp.allergens:
            parts.extend(bp.allergens)
        if bp.brand_preference:
            parts.extend(bp.brand_preference)
        if bp.category:
            parts.append(bp.category)
        if bp.medical_history:
            parts.extend(bp.medical_history)
        if bp.feeding_history:
            parts.extend(bp.feeding_history)
        if bp.health_notes and not (bp.medical_history or bp.feeding_history):
            parts.append(bp.health_notes)
        return " ".join(parts)

    async def answer(self, query: str, history: List[Dict[str, str]] = None,
                    constraints=None, baby_block: Optional[str] = None,
                    baby_profile: Optional[BabyProfile] = None) -> Answer:
        history = history or []
        # 1) 检索（Fix#1：用档案上下文增强查询，提升命中率）
        enriched_query = self._enrich_query(query, baby_profile)
        # F3 路由：按问题语义把查询映射到知识 kind，检索侧按 kind 加权/路由
        # （之前 retrieve 从不传 kind_filter/kind_weight，F3 在端到端是假绿）。
        kind_weight = intent_kind_weight(enriched_query) or intent_kind_weight(query)
        hits = self.store.retrieve(enriched_query, self.cfg.enterprise_id, top_k=5,
                                  kind_weight=kind_weight)
        # 2) 拼接上下文
        context = self._build_context(hits)
        # 3) 企业 prompt（含可选用户约束块 / 宝宝档案块）
        messages = self._build_messages(query, context, history, constraints, baby_block)
        # 4) 调用 LLM
        reply = await self.provider.complete(messages, retrieved_hits=hits)
        # D8 合规护栏：待确认（注册号/批准文号待填）商品不向客户主动推荐。
        # 命中里若含 pending 商品且回复中实际点名了该商品，则附合规提示，
        # 避免员工把未注册婴幼儿食品/保健品直接推荐给客户。
        pending_name = None
        for h in hits:
            nm = h.meta.get("name") or h.title
            if h.meta.get("pending") and nm and nm in reply:
                pending_name = nm
                break
        if pending_name is not None:
            reply = (reply.rstrip("。") +
                     f"。【合规提示：{pending_name} 注册号待确认，"
                     "暂不建议主动向客户推荐，请先在企业后台完成确认。】")
        # 5) 引用
        citations = [
            {"index": i + 1, "title": (h.meta.get("name") or h.title),
             "part": h.part, "chunk": h.chunk, "meta": h.meta}
            for i, h in enumerate(hits)
        ]
        # 6) 防幻觉/免责
        empty = len(hits) == 0
        if not reply.endswith(DISCLAIMER):
            reply = reply + " " + DISCLAIMER
        return Answer(text=reply, citations=citations, hits=hits, empty=empty)
