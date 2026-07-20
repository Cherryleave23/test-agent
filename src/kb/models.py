"""B 端产品结构化模型（数据模型 §4.1 / §4.2 已确认）。

奶粉 14 必填字段（products_milk）；营养品已确认字段（products_nutrition）。
尿不湿/服务为灵活 schema，归为 products_flex 通用键值表。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class MilkProduct:
    """奶粉（§4.1，14 必填字段）。"""

    name: str
    brand: str
    stage: str              # 1段/2段/3段
    age_range: str          # 0-6个月
    price: float
    origin: str             # 产地（生产地）
    milk_origin: str        # 奶源产地
    ptype: str              # 牛奶粉/羊奶粉/特配奶粉
    reg_number: str         # 国食注字
    manufacturer: str
    ingredients: str        # 完整配料表
    nutrition: str          # 完整营养成分
    highlights: str         # 优点/特色配方
    keywords: str = ""      # 来源侧打的标签/关键词（如 营养不良/挑食/偏瘦/全营养），差异化检索信号
    enterprise_id: str = ""
    id: Optional[int] = None

    def to_search_text(self) -> str:
        return (
            f"{self.name} {self.brand} {self.ptype} {self.stage}段 适用{self.age_range} "
            f"价格{self.price}元 产地{self.origin} 奶源{self.milk_origin} {self.highlights} "
            f"注册号{self.reg_number}"
        )

    def to_chunks(self) -> list:
        """语义分块：长说明书拆成「基础信息 / 配料表 / 营养成分 / 卖点」独立可检索块。

        每块独立向量化与关键词索引，细粒度参数查询（如 DHA 含量、是否含棕榈油）
        能命中对应块而非被整段平均语义稀释。每块 title 仍为产品名，便于召回后归并。

        基础信息携带「结构化字段 + 来源关键词」：关键词是差异化检索信号
        （如 全营养/营养不良），用于把参数级查询导向正确产品；而营销话术（highlights）
        单独留在 卖点 块并降权，避免雷同 boilerplate 干扰排序。
        """
        base = (
            f"{self.name} {self.brand} {self.ptype} {self.stage}段 适用{self.age_range} "
            f"价格{self.price}元 产地{self.origin} 奶源{self.milk_origin} "
            f"注册号{self.reg_number} 厂商{self.manufacturer} 关键词 {self.keywords}"
        )
        chunks = [("基础信息", base)]
        if self.ingredients:
            chunks.append(("配料表", f"{self.name} 配料表：{self.ingredients}"))
        if self.nutrition:
            chunks.append(("营养成分", f"{self.name} 营养成分：{self.nutrition}"))
        return chunks

    def meta(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k not in ("id",)}


@dataclass
class NutritionProduct:
    """营养品（§4.2，已确认）。"""

    name: str
    brand: str
    category: str           # 钙/DHA/益生菌/维生素/叶黄素/其他
    audience: str           # 婴幼儿/儿童/孕妇/成人
    dosage_form: str        # 粉剂/片剂/滴剂/胶囊/软糖
    age_range: str
    price: float
    origin: str
    manufacturer: str
    health_license: str     # 国食健字/卫食健字
    efficacy: str           # 核心功效/卖点
    ingredients: str
    nutrition: str
    highlights: str
    cautions: str           # 注意事项/禁忌
    keywords: str = ""      # 来源侧打的标签/关键词（差异化检索信号）
    enterprise_id: str = ""
    id: Optional[int] = None

    def to_search_text(self) -> str:
        return (
            f"{self.name} {self.brand} {self.category} {self.audience} {self.dosage_form} "
            f"适用{self.age_range} 价格{self.price}元 产地{self.origin} 功效{self.efficacy} "
            f"{self.highlights} 批号{self.health_license}"
        )

    def to_chunks(self) -> list:
        """语义分块：基础信息 / 成分 / 营养成分 / 卖点 / 注意事项。"""
        base = (
            f"{self.name} {self.brand} {self.category} {self.audience} {self.dosage_form} "
            f"适用{self.age_range} 价格{self.price}元 产地{self.origin} 功效{self.efficacy} "
            f"批号{self.health_license} 关键词 {self.keywords}"
        )
        chunks = [("基础信息", base)]
        if self.ingredients:
            chunks.append(("成分", f"{self.name} 成分：{self.ingredients}"))
        if self.nutrition:
            chunks.append(("营养成分", f"{self.name} 营养成分：{self.nutrition}"))
        if self.cautions:
            chunks.append(("注意事项", f"{self.name} 注意事项：{self.cautions}"))
        return chunks

    def meta(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k not in ("id",)}
