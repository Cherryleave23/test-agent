"""产品数据结构 schema 配置（零 src.* 依赖）。

将「奶粉 / 营养品」字段 schema 从硬编码双模型改为
**内置默认 + conf.yaml 可覆盖/增量** 的可配置体系，满足项目「企业自定义产品结构」硬要求：

- `load_schemas(conf_path)` 返回 {schema_name: SchemaDef}（合并 extends 继承）；
- `SchemaDef` 含 label / kind / fields / keywords / extends / auth_fields；
- 自定义类目（如某品牌只卖益生菌 `probiotic`）通过 `extends` 继承父类目字段，
  再追加自身字段，并用 `keywords` 供 classifier 识别；
- `auth_fields_for(category)` 给出某类目的权威字段（OCR/规则优先、冲突即 needs_review）。

conf.yaml 片段（与 classifier 的 product_categories 同文件）：
    product_schemas:
      probiotic:
        label: 益生菌
        kind: nutrition
        extends: nutrition
        keywords: [益生菌, probiotic, 菌群]
        fields:
          - {key: strain, label: 菌株, type: text}
          - {key: cfu, label: 活菌数(CFU), type: text}
"""
from __future__ import annotations

import copy
import os
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:  # yaml 缺失时退化为仅内置默认
    yaml = None  # type: ignore

logger = __import__("logging").getLogger(__name__)


# ---------------------------------------------------------------------------
# 内置默认字段（与 src/kb/models.py 的 MilkProduct / NutritionProduct 对齐）
# ---------------------------------------------------------------------------
# (key, label, type, required)
_MILK_FIELDS: List[tuple] = [
    ("name", "商品名", "text", True),
    ("brand", "品牌", "text", True),
    ("stage", "段位", "text", False),
    ("age_range", "适用年龄", "text", False),
    ("net_content", "净含量", "text", False),
    ("price", "价格", "number", False),
    ("origin", "产地", "text", False),
    ("milk_origin", "奶源产地", "text", False),
    ("ptype", "奶粉类型", "text", False),
    ("reg_number", "注册号", "text", False),
    ("manufacturer", "制造商", "text", False),
    ("ingredients", "配料表", "text", False),
    ("nutrition", "营养成分", "text", False),
    ("highlights", "卖点", "text", False),
    ("keywords", "关键词", "text", False),
]

_NUTRITION_FIELDS: List[tuple] = [
    ("name", "商品名", "text", True),
    ("brand", "品牌", "text", True),
    ("category", "类别", "text", False),
    ("audience", "适宜人群", "text", False),
    ("dosage_form", "剂型", "text", False),
    ("age_range", "适用年龄", "text", False),
    ("price", "价格", "number", False),
    ("origin", "产地", "text", False),
    ("manufacturer", "制造商", "text", False),
    ("health_license", "保健批号", "text", False),
    ("efficacy", "功效", "text", False),
    ("active_ingredients", "功效成分及含量", "text", False),
    ("dosage", "食用量/用法", "text", False),
    ("ingredients", "成分", "text", False),
    ("nutrition", "营养成分", "text", False),
    ("highlights", "卖点", "text", False),
    ("cautions", "注意事项", "text", False),
    ("keywords", "关键词", "text", False),
]


def _field(key: str, label: str, type_: str = "text", required: bool = False,
           aliases: Optional[List[str]] = None) -> dict:
    d = {"key": key, "label": label, "type": type_, "required": bool(required)}
    if aliases:
        d["aliases"] = list(aliases)
    return d


def _build_fields(spec: List[tuple]) -> List[dict]:
    return [_field(k, lbl, t, req) for (k, lbl, t, req) in spec]


# 权威字段：OCR/规则为事实源，LLM 不得覆盖；冲突即 needs_review
_MILK_AUTH = ("reg_number", "net_content", "stage")
_NUTRITION_AUTH = ("health_license", "net_content", "active_ingredients")

# 大类名 → 默认 schema 名
_CAT_TO_SCHEMA = {
    "配方粉": "milk",
    "奶粉": "milk",
    "milk": "milk",
    "营养品": "nutrition",
    "nutrition": "nutrition",
}


def _builtin_names() -> set:
    """返回内置（不可编辑）schema 名集合。"""
    return {"milk", "nutrition"}


def _builtin_schemas() -> Dict[str, dict]:
    return {
        "milk": {
            "label": "奶粉",
            "kind": "milk",
            "extends": None,
            "keywords": ["奶粉", "配方粉", "formula"],
            "auth_fields": list(_MILK_AUTH),
            "fields": _build_fields(_MILK_FIELDS),
        },
        "nutrition": {
            "label": "营养品",
            "kind": "nutrition",
            "extends": None,
            "keywords": ["DHA", "益生菌", "维生素", "钙", "铁", "锌", "营养", "nutrient"],
            "auth_fields": list(_NUTRITION_AUTH),
            "fields": _build_fields(_NUTRITION_FIELDS),
        },
    }


# ---------------------------------------------------------------------------
# 加载 / 合并
# ---------------------------------------------------------------------------
def load_schemas(conf_path: Optional[str] = None) -> Dict[str, dict]:
    """返回合并后的 {schema_name: SchemaDef}。

    内置默认垫底；conf.yaml 的 product_schemas 可覆盖整段或增量（extends 继承父类目字段）。
    """
    schemas = _builtin_schemas()
    data = _read_conf(conf_path)
    custom = (data or {}).get("product_schemas") or {}
    if not isinstance(custom, dict):
        return schemas
    # 先放无 extends 的；再解析有 extends 的（一次遍历足够：extends 必指向内置或已处理项）
    ordered = sorted(
        custom.items(),
        key=lambda kv: 0 if not kv[1].get("extends") else 1,
    )
    for name, spec in ordered:
        if not isinstance(spec, dict):
            continue
        parent = spec.get("extends")
        base_fields = list(schemas.get(parent, {}).get("fields", [])) if parent else []
        base_auth = list(schemas.get(parent, {}).get("auth_fields", [])) if parent else []
        base_keywords = list(schemas.get(parent, {}).get("keywords", [])) if parent else []
        base_kind = spec.get("kind") or schemas.get(parent, {}).get("kind", "flex")
        # 合并字段：按 key 去重，自定义字段追加（同名覆盖）
        merged: Dict[str, dict] = {f["key"]: dict(f) for f in base_fields}
        for f in (spec.get("fields") or []):
            if isinstance(f, dict) and f.get("key"):
                merged[f["key"]] = dict(f)
        auth = spec.get("auth_fields") or base_auth
        keywords = list(spec.get("keywords") or base_keywords)
        schemas[name] = {
            "label": spec.get("label", name),
            "kind": base_kind,
            "extends": parent,
            "keywords": keywords,
            "auth_fields": list(auth),
            "fields": list(merged.values()),
        }
    return schemas


def _default_conf_path() -> str:
    """conf.yaml 默认位置；可用环境变量 DATAPROC_CONF_PATH 覆盖（便于测试/端侧隔离）。"""
    env = os.environ.get("DATAPROC_CONF_PATH")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "conf.yaml")


def _read_conf(conf_path: Optional[str]) -> Optional[dict]:
    if not conf_path:
        conf_path = _default_conf_path()
    if not os.path.isfile(conf_path) or yaml is None:
        return None
    try:
        with open(conf_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("conf.yaml 读取失败: %s: %s", type(e).__name__, e)
        return None


def read_conf(conf_path: Optional[str] = None) -> dict:
    """读取 conf.yaml 全量；缺文件/缺 yaml 返回 {}。"""
    return _read_conf(conf_path) or {}


def write_product_schemas(schemas: dict, conf_path: Optional[str] = None) -> dict:
    """将自定义 product_schemas 写回 conf.yaml，**仅更新该段**，保留其余段（product_categories/llm/ocr…）。

    返回写入后的完整 conf。
    """
    if not conf_path:
        conf_path = _default_conf_path()
    if yaml is None:
        raise RuntimeError("PyYAML 未安装，无法写入 conf.yaml")
    current = read_conf(conf_path)
    current["product_schemas"] = schemas or {}
    os.makedirs(os.path.dirname(os.path.abspath(conf_path)), exist_ok=True)
    with open(conf_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(current, f, allow_unicode=True, sort_keys=False)
    return current


# ---------------------------------------------------------------------------
# 查询辅助
# ---------------------------------------------------------------------------
def resolve_schema_name(category: Optional[str],
                         schemas: Optional[Dict[str, dict]] = None) -> Optional[str]:
    """把传入的 category（大类名 或 schema 名）归一到 schema 名；未知返回原值（仍可作为 schema 名）。

    返回 None 表示调用方需自行推断（默认回退 milk）。
    """
    if category is None:
        return None
    schemas = schemas if schemas is not None else load_schemas()
    if category in schemas:
        return category
    return _CAT_TO_SCHEMA.get(category, category)


def schema_keys(category: Optional[str],
                schemas: Optional[Dict[str, dict]] = None) -> List[str]:
    """返回某 category/schema 合并后的字段键列表（去重、保序）。"""
    schemas = schemas if schemas is not None else load_schemas()
    name = resolve_schema_name(category, schemas)
    if name and name in schemas:
        return [f["key"] for f in schemas[name]["fields"]]
    # 兜底：按大类推断
    if category in ("营养品", "nutrition"):
        return [f["key"] for f in _build_fields(_NUTRITION_FIELDS)]
    return [f["key"] for f in _build_fields(_MILK_FIELDS)]


def auth_fields_for(category: Optional[str],
                    schemas: Optional[Dict[str, dict]] = None) -> tuple:
    """返回某类目的权威字段（OCR/规则优先、冲突即 needs_review）。"""
    schemas = schemas if schemas is not None else load_schemas()
    name = resolve_schema_name(category, schemas)
    if name and name in schemas:
        af = schemas[name].get("auth_fields")
        if af:
            return tuple(af)
    if category in ("营养品", "nutrition"):
        return tuple(_NUTRITION_AUTH)
    return tuple(_MILK_AUTH)


def is_nutrition(category: Optional[str],
                 schemas: Optional[Dict[str, dict]] = None) -> bool:
    """category 是否落入营养品系（kind==nutrition）。"""
    schemas = schemas if schemas is not None else load_schemas()
    name = resolve_schema_name(category, schemas)
    if name and name in schemas:
        return schemas[name].get("kind") == "nutrition"
    # 兜底：大类名
    return category in ("营养品", "nutrition")


def schema_kind(category: Optional[str],
                schemas: Optional[Dict[str, dict]] = None) -> str:
    """返回 category/schema 对应的入库 kind：milk | nutrition | flex。"""
    schemas = schemas if schemas is not None else load_schemas()
    name = resolve_schema_name(category, schemas)
    if name and name in schemas:
        return schemas[name].get("kind", "flex")
    if category in ("营养品", "nutrition"):
        return "nutrition"
    if category in ("配方粉", "奶粉", "milk"):
        return "milk"
    return "flex"


def schema_keywords(schemas: Optional[Dict[str, dict]] = None) -> List[tuple]:
    """返回 [(keyword, schema_name), ...]，供 classifier 做自定义类目识别。

    自定义（非内置）类目优先于内置（更具体的企业类目先匹配），长度 >= 2 的关键词才纳入。
    """
    schemas = schemas if schemas is not None else load_schemas()
    out: List[tuple] = []
    builtin = _builtin_names()
    # 先自定义后内置，保证企业自定义类目优先匹配
    for name, spec in schemas.items():
        if name in builtin:
            continue
        for kw in spec.get("keywords", []) or []:
            if isinstance(kw, str) and len(kw) >= 2:
                out.append((kw, name))
    for name, spec in schemas.items():
        if name in builtin:
            for kw in spec.get("keywords", []) or []:
                if isinstance(kw, str) and len(kw) >= 2:
                    out.append((kw, name))
    return out


# 兼容旧调用：骨架常量（奶粉 7 字段；营养品全字段）
MILK_KEYS: List[str] = [f[0] for f in _MILK_FIELDS]
NUTRITION_KEYS: List[str] = [f[0] for f in _NUTRITION_FIELDS]
