"""用户约束模型与抽取/压缩（MOD-session P1 扩展：规划·方向B + 记忆·方向A）。

本模块是对标分析选定的两项高 ROI 改进的统一载体——二者收敛为同一产物
`UserConstraints`（结构化用户约束）：

- **方向 B · 条件抽取与累积（规划）**：`extract_constraints(text)` 用规则从单句用户消息抽取
  约束（月龄/段位/过敏原/预算/品类），`merge` 逐轮累积。**无 LLM 依赖、确定性**。
- **方向 A · 短期记忆摘要压缩（记忆）**：`summarize_to_constraints(history, provider)` 在会话超
  N 轮时调用 LLM 把早期对话压成结构化约束（**限制有效信息量，非轮数**）；JSON 解析失败兜底
  退化为规则抽取。

约束 schema 复用 `MilkProduct` 字段词表（stage / age_range→baby_age / price→budget /
brand / ptype→category），与母婴领域一致，便于后续与产品过滤联动。

设计约束（PRD non-goals）：
- 不引入 Hermes Ralph Loop / Kanban / OpenClaw Obsidian Vault（过度工程）。
- 抽取/压缩产物仅作 prompt 注入的辅助信号，绝不替代「企业知识库」事实来源。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

# 过敏原词表（规则抽取用；可随企业词库扩展）
_ALLERGEN_VOCAB = [
    "牛奶蛋白", "乳糖", "鸡蛋", "蛋清", "花生", "海鲜", "鱼", "虾",
    "大豆", "坚果", "芒果", "麸质", "芒果类",
]
# 品类映射（关键词 → 规范化品类）
_CATEGORY_MAP = [
    ("奶粉", "奶粉"), ("配方奶", "奶粉"), ("配方奶粉", "奶粉"),
    ("营养品", "营养品"), ("DHA", "营养品"), ("益生菌", "营养品"),
    ("钙", "营养品"), ("维生素", "营养品"),
    ("尿不湿", "尿不湿"), ("纸尿裤", "尿不湿"), ("拉拉裤", "尿不湿"),
]

# 方向 A 触发阈：会话历史轮数超过该值才触发 LLM 压缩（限制有效信息量，非轮数）
SUMMARY_THRESHOLD = 10


@dataclass
class UserConstraints:
    """结构化用户约束（复用 MilkProduct 词表）。空对象表示尚未抽取到任何约束。"""

    baby_age: str = ""                 # 月龄/年龄段，如 "6个月" / "0-6个月"
    stage: str = ""                    # 段位，如 "1段"
    allergens: List[str] = field(default_factory=list)
    budget: Optional[float] = None     # 预算上限（元）
    brand_preference: List[str] = field(default_factory=list)
    category: str = ""                 # 品类倾向：奶粉/营养品/尿不湿
    notes: str = ""                    # 其他未结构化约束/自由文本

    def is_empty(self) -> bool:
        return not (
            self.baby_age or self.stage or self.allergens
            or self.budget is not None or self.brand_preference
            or self.category or self.notes
        )

    def merge(self, other: "UserConstraints") -> "UserConstraints":
        """累积合并：新约束刷新旧值；列表类去重保序合并；budget 以非 None 者为准。"""
        merged = UserConstraints()
        for f in ("baby_age", "stage", "category", "notes"):
            setattr(merged, f, getattr(other, f) or getattr(self, f))
        for f in ("allergens", "brand_preference"):
            combined = list(getattr(self, f)) + list(getattr(other, f))
            merged.__dict__[f] = list(dict.fromkeys(combined))  # 去重保序
        merged.budget = other.budget if other.budget is not None else self.budget
        return merged

    def to_prompt_block(self) -> str:
        """生成注入 system prompt 的「【用户已明确约束】」块；空则返空串。"""
        if self.is_empty():
            return ""
        lines = ["【用户已明确约束】"]
        if self.baby_age:
            lines.append(f"- 宝宝月龄/年龄段：{self.baby_age}")
        if self.stage:
            lines.append(f"- 段位：{self.stage}")
        if self.allergens:
            lines.append(f"- 过敏原：{', '.join(self.allergens)}")
        if self.budget is not None:
            lines.append(f"- 预算上限：{self.budget:g} 元")
        if self.brand_preference:
            lines.append(f"- 品牌偏好：{', '.join(self.brand_preference)}")
        if self.category:
            lines.append(f"- 品类倾向：{self.category}")
        if self.notes:
            lines.append(f"- 其他：{self.notes}")
        lines.append("（回答时请优先满足上述约束；若约束与知识库冲突，以知识库事实为准）")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "UserConstraints":
        if not s:
            return cls()
        try:
            d = json.loads(s)
        except (json.JSONDecodeError, TypeError):
            return cls()
        if not isinstance(d, dict):
            return cls()
        known = set(cls.__dataclass_fields__.keys())
        clean = {k: v for k, v in d.items() if k in known}
        # 列表字段容错
        for f in ("allergens", "brand_preference"):
            if not isinstance(clean.get(f), list):
                clean[f] = []
        if "budget" in clean and clean["budget"] not in (None, ""):
            try:
                clean["budget"] = float(clean["budget"])
            except (TypeError, ValueError):
                clean["budget"] = None
        return cls(**clean)


# ---------------------------------------------------------------------------
# 方向 B · 规则化逐轮抽取（确定性，无 LLM）
# ---------------------------------------------------------------------------
def extract_constraints(text: str) -> UserConstraints:
    """从单句用户消息规则抽取约束。返回可能为空的 UserConstraints。"""
    c = UserConstraints()
    if not text:
        return c
    # 月龄/年龄段：6个月 / 0-6个月 / 6月 / 半岁
    m = re.search(r"(\d+\s*[-~至到]?\s*\d*\s*个?\s*月|半岁|\d+\s*周|\d+\s*岁)", text)
    if m:
        c.baby_age = re.sub(r"\s+", "", m.group(1))
    # 段位：1段 / 一段
    m = re.search(r"(?<!\d)(\d)\s*段", text)
    if m:
        c.stage = f"{m.group(1)}段"
    # 预算：预算300 / 300元 / 300以内 / 不超300
    m = (re.search(r"预算[^\d]*?(\d+)", text)
         or re.search(r"(\d+)\s*元", text)
         or re.search(r"(\d+)\s*(以内|以下|内)", text))
    if m:
        try:
            c.budget = float(m.group(1))
        except ValueError:
            c.budget = None
    # 过敏原
    for a in _ALLERGEN_VOCAB:
        if a in text:
            c.allergens.append(a)
    # 品类
    for kw, cat in _CATEGORY_MAP:
        if kw in text:
            c.category = cat
            break
    return c


# ---------------------------------------------------------------------------
# 方向 A · LLM 摘要压缩（超 N 轮触发，限制有效信息量）
# ---------------------------------------------------------------------------
def _parse_constraints_json(raw: str) -> UserConstraints:
    """从 LLM 输出解析 JSON 约束；失败兜底退化为规则抽取。"""
    if not raw:
        return UserConstraints()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        parsed = UserConstraints.from_json(m.group(0))
        if not parsed.is_empty():
            return parsed
    # 兜底：把原文当对话文本规则抽取
    return extract_constraints(raw)


async def summarize_to_constraints(history_text: str, provider) -> UserConstraints:
    """用 LLM 把早期对话压缩为结构化约束（方向 A）。

    - `history_text`：待压缩的早期对话文本（每行 `role: content`）。
    - `provider`：具备 `async complete(messages) -> str` 的 LLM provider（Ollama/Cloud/Mock 均可）。
    - 返回结构化 `UserConstraints`；JSON 解析失败兜底退化为规则抽取。
    """
    messages = [
        {
            "role": "system",
            "content": (
                "你是母婴导购的约束抽取器。把对话压缩为 JSON 结构化约束，"
                "字段：baby_age(字符串), stage(字符串), allergens(字符串数组), "
                "budget(数字或null), brand_preference(字符串数组), category(字符串), notes(字符串)。"
                "只输出一个 JSON 对象，不要任何解释或代码块标记。"
            ),
        },
        {"role": "user", "content": history_text},
    ]
    raw = await provider.complete(messages)
    return _parse_constraints_json(raw)


def should_compress(history_len: int, threshold: int = SUMMARY_THRESHOLD) -> bool:
    """是否触发 LLM 压缩：历史轮数超过阈值才压缩（限制有效信息量，非轮数）。"""
    return history_len >= threshold
