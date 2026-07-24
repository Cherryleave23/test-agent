# 营养品数据结构 + 可配置 schema 设计（分类感知抽取 + conf.yaml + WEBUI 配置）

> 决策（用户 2026-07-23 确认）：
> 1. `NutritionProduct` **补充** `active_ingredients`（功效成分及含量）+ `dosage`（食用量/用法）两个高频字段；
> 2. 抽取层**改成分类感知**：营养品经 OCR/爬虫也带全字段；
> 3. 字段 schema **conf.yaml 可自定义**（企业按自身产品结构增类目/字段），且**在导入工具 WEBUI 中提供配置面板**（落盘 `conf.yaml`）。
>
> 状态：**✅ 已落地**（CVC：框架/接口先定 → 编码 → `harness/test_nutrition_schema.py` NS1–NS7 全绿，奶粉路径回归零退化）。

## P1 — 意图（缺口与性质）

| # | 缺口 | 性质 |
|---|---|---|
| G1 | `NutritionProduct` 缺 `active_ingredients` / `dosage`，母婴营养最高频的"成分含量/怎么吃"无法结构化检索 | **模型补全** |
| G2 | `structurer.FIELD_KEYS` 仅 7 个奶粉字段，营养品经 OCR/爬虫抽取时 `category/audience/dosage_form/health_license/efficacy/cautions/active_ingredients/dosage` 全丢 | **核心缺陷** |
| G3 | 抽取未分类感知；`_rule_extract` / `_fuse` 权威字段仅奶粉语义（`reg_number/net_content/stage`） | **合规增强** |
| G4 | 字段 schema 硬编码双模型，不支持企业自定义（项目硬要求"定制其数据库"） | **定制化** |
| G5 | WEBUI 无产品 schema 配置入口（用户要求在导入工具 GUI 配置类目/字段） | **GUI 交付** |

Non-goals：换存储引擎；动 `schema.py` 的 `ProductRecord` 泛型（fields 已是 dict，天然承载自定义字段）；真实联网抓取（爬虫为独立获取工具，见 `CRAWLER_DESIGN.md`）。

## P2 — 框架（复用 + 可控新增）

- `src/kb/models.py`：`NutritionProduct` 增 `active_ingredients: str = ""`、`dosage: str = ""`；同步进 `to_search_text` / `to_chunks`（成分含量块、食用量块）。
- `tools/dataproc/structurer.py`：
  - 拆 `FIELD_KEYS` 为 `MILK_KEYS` / `NUTRITION_KEYS`（NUTRITION_KEYS 含全部营养字段 + 新增两项）；
  - `structure(text, provider, category=None)`：按 `category` 选键集（默认用 classifier 推断）；
  - `_rule_extract(text, category=None)`：增营养规则（保健批号 / 适宜人群 / 功效 / 功效成分含量 / 食用量）；
  - `_OCR_AUTH_FIELDS` 改由 `auth_fields_for(category)` 决定（奶粉=reg_number/net_content/stage；营养品=health_license/net_content/active_ingredients）；
  - LLM system prompt 按 category 动态列出键集。
- `tools/dataproc/schema_conf.py`（新增，零 `src.*`）：`load_schemas(conf_path) -> {name: SchemaDef}`，合并**内置默认 schema（milk/nutrition）** 与 `conf.yaml` 的 `product_schemas`（支持自定义类目、`extends` 继承、每类目 `keywords` 供分类器识别）。
- `tools/dataproc/classifier.py`：自定义类目识别——读 `schema_conf` 中各 schema 的 `keywords`（缺省回退到 label 关键词），与既有 `product_categories` 覆盖并存。
- `tools/dataproc/build.py`：调 `structure(content, provider, category=cls["product_category"])`，把分类结果传入抽取；`kind` 路由保持（milk/nutrition/flex）。
- `tools/dataproc/gui/backend`：新增 `GET /settings/schema`、`POST /settings/schema`——读/写 `conf.yaml` 的 `product_schemas`（只更新该段，保留 `product_categories`/`llm`/`ocr` 等）。
- `tools/dataproc/gui/frontend`：新增 `SchemaSettingsPanel.tsx` + nav 按钮「🏷️ 产品数据结构」+ `api.ts` 的 `getSchema`/`updateSchema`。

## P3 — 接口与契约

### G1 模型（src/kb/models.py）
```python
@dataclass
class NutritionProduct:
    name: str; brand: str; category: str; audience: str; dosage_form: str
    age_range: str; price: float; origin: str; manufacturer: str
    health_license: str; efficacy: str
    active_ingredients: str = ""   # 新增：标志性/功效成分及含量，如「每粒含 DHA 100mg、钙 300mg、益生菌 100 亿 CFU」
    dosage: str = ""               # 新增：食用量/用法，如「每日 1-2 粒，温水送服」
    ingredients: str; nutrition: str; highlights: str; cautions: str
    keywords: str = ""
    enterprise_id: str = ""; id: Optional[int] = None
```
`to_chunks` 增「功效成分」块（active_ingredients）、「食用量」块（dosage），供细粒度检索。

### G2/G3 分类感知抽取（structurer.py）
```python
MILK_KEYS = ["name","brand","stage","age_range","net_content","price","origin",
             "milk_origin","ptype","reg_number","manufacturer","ingredients",
             "nutrition","highlights","keywords"]
NUTRITION_KEYS = ["name","brand","category","audience","dosage_form","age_range","price",
                  "origin","manufacturer","health_license","efficacy",
                  "active_ingredients","dosage","ingredients","nutrition",
                  "highlights","cautions","keywords"]

def structure(text, provider=None, category=None) -> StructuringResult:
    keys = NUTRITION_KEYS if (category in NUTRITION_CATS) else MILK_KEYS
    rule = _rule_extract(text, category)
    ...  # LLM prompt 仅列 keys；_fuse 用 auth_fields_for(category)

def auth_fields_for(category) -> tuple:
    if category in NUTRITION_CATS:
        return ("health_license", "net_content", "active_ingredients")
    return ("reg_number", "net_content", "stage")
```
`_rule_extract` 营养增量：
- `health_license`：`(国食健字|卫食健字|食健备|国食健备)\s*[GJ]?\s*[\w\d]+` 或 `SC\d+`（普通食品）；
- `audience`：`适宜人群[:：]?\s*([^\n；;]+)`；
- `efficacy`：`保健功能[:：]?\s*([^\n；;]+)` 或功效词表（增强免疫力/骨骼/视力…）；
- `active_ingredients`：`每[粒片袋瓶支]\s*含?\s*([^\n；;]+?)(?=\n|；|;|$)`（功效成分含量模式）；
- `dosage`：`每日[1-9]\d*\s*[粒片袋瓶支]|食用量[:：]?\s*([^\n；;]+)`。

### G4 conf.yaml 可配置 schema
`conf.yaml`（与 classifier 的 `product_categories` 同文件）：
```yaml
product_categories:        # 既有：ptype/关键词 → 大类 覆盖
  牛奶粉: 配方粉
  DHA: 营养品
product_schemas:           # 新增：企业自定义产品数据结构
  milk:
    label: 奶粉
    kind: milk
    fields:
      - {key: name, label: 商品名, type: text, required: true}
      - {key: stage, label: 段位, type: text}
      # ... 其余 MILK_KEYS 字段
  nutrition:
    label: 营养品
    kind: nutrition
    fields:
      - {key: name, label: 商品名, type: text, required: true}
      - {key: active_ingredients, label: 功效成分及含量, type: text}
      - {key: dosage, label: 食用量/用法, type: text}
      # ... 其余 NUTRITION_KEYS 字段
  probiotic:               # 企业自定义子类（示例：某品牌只卖益生菌）
    label: 益生菌
    kind: nutrition        # 复用营养品入库/检索
    extends: nutrition
    keywords: [益生菌, probiotic, 菌群]
    fields:
      - {key: strain, label: 菌株, type: text}
      - {key: cfu, label: 活菌数(CFU), type: text}
```
`schema_conf.load_schemas()`：内置默认（milk/nutrition 全字段）垫底，`conf.yaml` 覆盖/增量合并；`extends` 继承父 schema 字段后再追加。

### G5 WEBUI 配置面板
- 后端 `GET /settings/schema` → 返回当前 schema 列表（含内置默认 + conf.yaml 覆盖）；`POST /settings/schema` → 接收编辑后的 schemas，仅覆写 `conf.yaml` 的 `product_schemas` 段（保留其余段），校验 key 合法（字母/下划线、非空）。
- 前端 `SchemaSettingsPanel`：列类目卡片，每卡可增删字段（key/label/type/required），「保存」→ `updateSchema`；「重置为默认」→ 清 conf.yaml 该段。

## P5 — 验收（harness，必须 RUN PASS/FAIL）
`harness/test_nutrition_schema.py`：

| 测试 | 验证点 | 判定 |
|---|---|---|
| NS1 | `NutritionProduct` 含 `active_ingredients` / `dosage`；`to_chunks` 产出「功效成分」「食用量」块 | PASS/FAIL |
| NS2 | `structure(text, MockProvider(json), category="营养品")` 产出 `NUTRITION_KEYS` 全字段（含 active_ingredients/dosage），非奶粉字段集 | PASS/FAIL |
| NS3 | `structure(text, MockProvider(json), category="配方粉")` 仍走 `MILK_KEYS`（stage/reg_number 等），不因营养品改动退化 | PASS/FAIL |
| NS4 | `_rule_extract(营养品文本, category="营养品")` 规则抽出 health_license/audience/efficacy/active_ingredients/dosage | PASS/FAIL |
| NS5 | `_fuse` 权威字段按 category：营养品 health_license 冲突→保留规则值 + `needs_review`；milk reg_number 冲突同理 | PASS/FAIL |
| NS6 | `load_schemas` 加载 conf.yaml 自定义类目 `probiotic`（extends nutrition + strain/cfu）；`classifier` 用其 `keywords` 识别；`structure`/`_rule_extract` 在该类目下抽 strain/cfu | PASS/FAIL |
| NS7 | WEBUI：`GET /settings/schema` 返回 schema；`POST /settings/schema` 写入 `conf.yaml`；再次 `GET` 反映改动；`conf.yaml` 其他段（llm/ocr）未被破坏 | PASS/FAIL |

**闸门**：任一 FAIL ⇒ 未完成；全绿 ⇒ 营养品数据结构 + 可配置 schema 可用（"同一接口"对营养品真正成立）。
**回归**：跑 `harness/test_paradigm2.py`、`harness/test_dataproc_resolver.py`、`harness/test_dataproc_ocr.py`、`harness/test_ingest.py`、`harness/test_llm_settings.py`；重点确认奶粉路径（NS3）零退化。

> **验收记录（2026-07-23）**：NS1–NS7 全部 PASS；回归集（paradigm2 / resolver / ocr / ingest / llm_settings / gui_backend / dataproc_pdf）全绿，奶粉抽取路径零退化。

## 后续（非本阶段，列出供排期）
- F1：自定义字段持久化——`products_milk`/`products_nutrition` 增 `extra_json` 列存越界字段；或自定义 `kind=flex` 路由 `products_flex` 键值表（models.py 已预留）。当前 v1：自定义字段保留在 bundle `ProductRecord.fields`（可移植）+ 注入 corpus `meta`（可检索），类型化表仅存标准列。
- F2：GUI schema 编辑的强校验（type 枚举、required 约束、与抽取 prompt 同步）。
- F3：custom category 的实体解析（`resolve`）用其专属键（如 strain+cfu）兜底。
- F4：crawler/structurer 已就位，营养品经爬虫→手动放入产品资料→build 即走本设计的分类感知抽取。
