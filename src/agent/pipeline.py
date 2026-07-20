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


DISCLAIMER = "（温馨提示：以上为产品信息参考，不构成医疗诊断，如有健康问题请咨询专业医生。）"


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
        system_parts = [
            f"{self.cfg.system_prompt}\n"
            "仅依据下方【企业知识库】内容回答，不得编造未提及的信息；"
            "如知识库无相关内容，明确告知用户暂无信息。",
            f"【企业知识库】\n{context}",
        ]
        # P1 约束块注入（方向 B 累积 + 方向 A 压缩的产物）：可选，空则不注入
        if constraints is not None:
            block = constraints.to_prompt_block()
            if block:
                system_parts.append(block)
        # MOD-baby-profile：当前焦点宝宝档案块注入（可选，空则不注入）
        if baby_block:
            system_parts.append(baby_block)
        system = "\n\n".join(system_parts)
        msgs: List[Dict[str, str]] = [{"role": "system", "content": system}]
        for h in history:
            msgs.append({"role": h["role"], "content": h["content"]})
        msgs.append({"role": "user", "content": query})
        return msgs

    async def answer(self, query: str, history: List[Dict[str, str]] = None,
                    constraints=None, baby_block: Optional[str] = None) -> Answer:
        history = history or []
        # 1) 检索
        hits = self.store.retrieve(query, self.cfg.enterprise_id, top_k=5)
        # 2) 拼接上下文
        context = self._build_context(hits)
        # 3) 企业 prompt（含可选用户约束块 / 宝宝档案块）
        messages = self._build_messages(query, context, history, constraints, baby_block)
        # 4) 调用 LLM
        reply = await self.provider.complete(messages, retrieved_hits=hits)
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
