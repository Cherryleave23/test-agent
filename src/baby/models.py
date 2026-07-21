"""宝宝/客户档案数据模型（MOD-baby-profile，P2）。

字段复用 MilkProduct 词表（stage / age_range→baby_age / price→budget / brand→brand_preference /
ptype→category），与既有 UserConstraints 同源，保证抽取/注入语义一致。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Customer:
    """客户（宝宝归属人，1→N 宝宝）。"""

    customer_id: Optional[int]
    enterprise_id: str
    employee_id: str
    name: str                 # 客户名/称呼（张姐）
    phone: str = ""
    notes: str = ""


@dataclass
class BabyProfile:
    """单个宝宝的持久档案（复用 MilkProduct 词表）。"""

    baby_id: Optional[int]
    enterprise_id: str
    employee_id: str
    customer_id: int
    name: str                # 宝宝名/昵称（壮壮）
    baby_age: str = ""       # 月龄/年龄段（复用 UserConstraints）
    gender: str = ""
    stage: str = ""          # 段位
    allergens: List[str] = field(default_factory=list)
    budget: Optional[float] = None
    brand_preference: List[str] = field(default_factory=list)
    category: str = ""       # 奶粉/营养品/尿不湿
    health_notes: str = ""   # 健康备注/喂养方式（自由文本兜底，向后兼容）
    # P2-v2 schema 扩展（2026-07-21）：结构化医学/喂养字段
    birth_date: str = ""                          # ISO 日期 "2025-05-21"
    gestational_weeks: Optional[int] = None       # 孕周（如 35；<37 为早产）
    medical_history: List[str] = field(default_factory=list)   # 医疗史 ["早产35周","出生5.18斤"]
    feeding_history: List[str] = field(default_factory=list)   # 喂养史 ["混合喂养→纯奶粉"]
    status: str = "pending"  # pending(待确认) | confirmed

    def is_empty_attr(self) -> bool:
        return not (
            self.baby_age or self.gender or self.stage or self.allergens
            or self.budget is not None or self.brand_preference
            or self.category or self.health_notes
            or self.birth_date or self.gestational_weeks is not None
            or self.medical_history or self.feeding_history
        )

    def merge(self, other: "BabyProfile") -> "BabyProfile":
        """累积合并（复用 UserConstraints.merge 语义）：新刷新旧；列表去重保序；budget 非 None 优先。"""
        merged = BabyProfile(
            baby_id=self.baby_id, enterprise_id=self.enterprise_id,
            employee_id=self.employee_id, customer_id=self.customer_id,
            name=self.name, status=self.status,
        )
        for f in ("baby_age", "gender", "stage", "category", "health_notes",
                  "birth_date"):
            setattr(merged, f, getattr(other, f) or getattr(self, f))
        # gestational_weeks：非 None 优先（与 budget 同语义）
        merged.gestational_weeks = (other.gestational_weeks
                                    if other.gestational_weeks is not None
                                    else self.gestational_weeks)
        for f in ("allergens", "brand_preference", "medical_history", "feeding_history"):
            combined = list(getattr(self, f)) + list(getattr(other, f))
            merged.__dict__[f] = list(dict.fromkeys(combined))  # 去重保序
        merged.budget = other.budget if other.budget is not None else self.budget
        if other.status == "confirmed":  # 任一侧已确认则保持 confirmed
            merged.status = "confirmed"
        return merged

    def to_prompt_block(self, customer_name: str = "") -> str:
        """生成注入 system prompt 的「【当前宝宝档案】」块；空属性不列。"""
        lines = ["【当前宝宝档案】"]
        who = f"{self.name}"
        if customer_name:
            who += f"（客户：{customer_name}）"
        lines.append(f"- 宝宝：{who}")
        if self.gender:
            lines.append(f"- 性别：{self.gender}")
        if self.birth_date:
            lines.append(f"- 出生日期：{self.birth_date}")
        if self.baby_age:
            lines.append(f"- 月龄/年龄段：{self.baby_age}")
        if self.gestational_weeks is not None:
            lines.append(f"- 孕周：{self.gestational_weeks}" +
                         ("（早产）" if self.gestational_weeks < 37 else ""))
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
        # 优先展示结构化字段（Fix#4）；health_notes 作为兜底
        if self.medical_history:
            lines.append(f"- 医疗史：{'; '.join(self.medical_history)}")
        if self.feeding_history:
            lines.append(f"- 喂养史：{'; '.join(self.feeding_history)}")
        if self.health_notes and not (self.medical_history or self.feeding_history):
            lines.append(f"- 健康/喂养备注：{self.health_notes}")
        if self.status == "pending":
            lines.append("（该档案为自动建档待确认，如有误请告知我修正/合并/删除）")
        lines.append("（回答时请优先满足上述宝宝情况；若与知识库冲突，以知识库事实为准）")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)
