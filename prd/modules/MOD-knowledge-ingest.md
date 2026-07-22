# MOD-knowledge-ingest 模块详解（知识转化与采集层）

> 依据 charter C3 / C5：知识来源 = **PDF/说明书 + 图片表格 + 爬虫（官网网页）**，均为非结构化，
> **首版不含结构化 API 适配器**。配套**知识转化工具链**（爬虫 / OCR / 多源统一接口）把散落知识归一后
> **经产物契约（NDJSON bundle）写入 MOD-kb**。**方案 B 完全自研**（不依赖 Hermes）。本文件为**可实现规格**。
>
> **架构边界（重要）**：本模块跨越两个**有界上下文**——① **standalone 数据处理工具 `tools/dataproc/`**（可单独在某台设备运行，
> 负责 爬虫 / OCR / 结构化抽取 / 商品实体解析 / 分类，产出中性 bundle）；② **agent 端导入器 `src/ingest/importer.py`**
> （负责把 bundle 载入 `store` 含向量化/索引）。二者**彻底隔离：工具不 `import src.*`、agent 不 `import` 工具，仅以产物契约为边界**（见「〇·产物契约」）。

## 职责
把企业散落的非结构化知识（说明书 PDF、产品图、规格表格、官网商品/知识页）转化为结构化知识记录，
经**统一适配接口**归一后写入知识库（MOD-kb）。对本模块而言，下游知识库是黑盒契约。

> **B 端结构化产品**：奶粉/营养品等可能需从产品册 PDF/图片**抽取结构化字段**填入
> `prd/references/data-model.md` 定义的品类 schema（如奶粉 14 必填字段）。**字段来源与入库方式见
> Q-DB1，**已定（见「〇·P3」商品实体解析与分类，落在 standalone 工具 `tools/dataproc/`；产物经「〇·产物契约」入 agent）**——解析抽取路径：PDF/图片 OCR → 结构化字段 → 商品实体解析/分类（reg_number 优先 + 元组兜底、pending 建档）；Excel/ERP 仍作后续可选来源。

---

## 〇、本次实施 P1（扩展 MOD-knowledge-ingest，意图先行）

> 分类：**P4 扩展**（非新模块、非纯 bugfix、非纯重构）。在既有模块上落地「统一多源适配接口」的第一块地基 + 第一个真实非结构化适配器。
> 依据 CVC：先写意图/非目标，再写代码；每行为配 harness；PRD 与 harness 为单一事实源。

### 意图（must-have）
1. **统一接口 + 注册表**：定义 `IngestAdapter` 协议（`fetch() -> List[KnowledgeRecord]`），所有来源归一为同一 `KnowledgeRecord` 结构；`IngestPipeline` 提供 `register`/`run`，开闭原则（新增来源不动核心）。
2. **把现有 markdown 商品适配器纳为「统一接口」的一种实现**：新增 `MarkdownProductAdapter` 包装既有 `parse_md_product`，产出 `source_type="milk"` 的 `KnowledgeRecord`（`structured` 持有 `MilkProduct`）。`parse_md_product`/`ingest_markdown_products` **保留不动**（向后兼容，只增不删）。
3. **第一个真实非结构化适配器：`WebCrawlerAdapter`**：用标准库 `urllib` + `html.parser`（零外部依赖，端侧友好）真实抓取并解析 HTML，产出 `source_type="web"` 的内容分块。沙箱内**以本地 stub HTTP 服务驱动真实客户端代码路径**（沿用 MOD-wechat §五 既定做法），不下真实外网。
4. **`IngestPipeline` 路由 + 去重 + 容错**：按 `source_type` 路由到对应 sink（`milk`→`store.add_milk`；`nutrition`→`store.add_nutrition`；`web`/`text`/`hq`→`store.add_knowledge`/`add_hq_knowledge`）；**跨运行内容哈希去重**（新增 `ingest_dedup` 表）；**单适配器失败不中断整批、失败留痕**（不静默丢弃，不谎称成功）。

### 非目标（non-goals，本次不做）
- **不做 OCR / PDFAdapter 真实实现**（仍 `NotImplementedError`，留待后续；符合「采集是搬运非生成」与端侧轻量约束）。
- **不碰已绿的检索/生产闭环**（MOD-kb / MOD-agent / MOD-wechat 既有 harness 不变）；本 P1 仅以只读方式桥接既有 `store.retrieve` 做集成断言。
- **不引入 LLM 改写采集内容**（母婴事实性内容禁止生成式改写，见 §六 风险）。

### 文件与 harness 落点
| 文件 | 动作 | 说明 |
|------|------|------|
| `src/ingest/protocol.py` | 改 | `KnowledgeRecord` 增 `structured`/`product_category`；移除 3 个 `NotImplementedError` 占位类，指向 `adapters.py` |
| `src/ingest/adapters.py` | 新 | `MarkdownProductAdapter`（包装既有 `parse_md_product`）+ 真实 `WebCrawlerAdapter` + `IngestPipeline` + `REGISTRY` |
| `src/ingest/markdown_product.py` | 不变 | `parse_md_product` / `ingest_markdown_products` 保留（向后兼容，只增不删） |
| `src/kb/store.py` | 改（增） | 新增 `add_knowledge` + `ingest_dedup` 表 + `is_ingested`/`mark_ingested` |
| `harness/test_ingest.py` | 新 | `@module ingest`：I1 爬虫 / I2 markdown / I3 归一 / I4 去重 / I5 容错 / I6 集成 |

> 状态：**in-progress（P1）**。`02-index.md` 中 MOD-knowledge-ingest 由 `backlog` 升级为 `partial`。

---

## 〇·P2、本次实施（OCR/PDF 适配器真实落地 —— 落在 standalone 数据处理工具 `tools/dataproc/`）

> 分类：**P4 扩展**（在统一接口 + 爬虫地基上，落地 OCR/PDF 真实适配器）。
> **架构边界（本次关键变更）**：自用户确认「数据工具与 agent 彻底隔离」后，OCR/PDF 适配器**不再位于 `src/ingest/`**，
> 而是实现于**独立可单独运行的工具 `tools/dataproc/`**（独立 `pyproject`、严禁 `import src.*`）。
> 工具把 PDF/图片/网页变成**中性产物（NDJSON bundle，见「〇·产物契约」）**；agent 端 `src/ingest/importer.py` 仅负责把产物载入
> `store`（含向量化/索引，见「〇·P4」）。二者**物理/进程隔离，仅以产物契约为边界**。
> 决策（已确认）：引擎 = **PaddleOCR + PP-Structure**；端侧**可选安装**；harness **默认轻量 fixture 绿跑 + 真实 `RUN_REAL_OCR=1` 门控**；范围**仅批量/配置 source，不做微信发图入口**。

### 意图（must-have）
1. **`tools/dataproc/adapters/pdf.py` 真实实现**：数字 PDF（`pypdf` 抽文本层，不浪费 OCR）与扫描件（无文本层 → PaddleOCR）两路；含表格页经 PP-Structure 抽 `table_cells`。产出工具内 `CorpusRecord`（`part=b_kb`/源类型标记）。
2. **`tools/dataproc/adapters/image_table.py` 真实实现（含预处理子流程）**：产品图/规格表/电商详情页长图 → opencv 预处理（resize+灰度+CLAHE+de-skew）→ PaddleOCR → 坐标排序阅读顺序 → PP-Structure；长图纵向切片；无文字/低置信标 `low_conf`、**绝不编造**。
3. **端侧可选安装**：OCR 依赖作 Tier1 大文件在 `deploy/dependency-manifest.yaml` 声明；`ocr_enabled=false` 的实例不触发 OCR 路径（工具本身可选装，无 OCR 依赖也能跑纯文本/结构化源）。
4. **工具内归一**：各异构源归一为工具自管 schema（`dataproc/schema.py` 的 `ProductRecord` / `CorpusRecord`），**不 import `src.kb`/`src.agent`**；归一到产物契约（见下「〇·产物契约」）。
5. 每行为配 harness（CVC 只增不删）。

### 非目标（non-goals）
- **不写 agent 的 SQLite/Chroma**：写入是 importer 职责（「〇·P4」）；工具只产文件。
- **不做向量化/embedding**：embedding 在 agent 端 `store.add_*` 时做（模型与运行时在 agent 侧）。
- **不引入 MinerU 默认**（可选重 Tier deferred）；**不用 LLM 改写 OCR 事实**；**不碰已绿闭环**。
- **不做"微信发图 → KB 入库"交互入口**（批量/配置 source 专属）；**可选重 Tier（deferred）= Unlimited-OCR / MinerU**（需 GPU，非默认）。

### 文件与 harness 落点
| 文件 | 动作 | 说明 |
|------|------|------|
| `tools/dataproc/adapters/pdf.py` | 新 | `PDFAdapter`：数字直抽 / 扫描件 OCR / 表格 PP-Structure，产出 `CorpusRecord` |
| `tools/dataproc/adapters/image_table.py` | 新 | `ImageTableAdapter`：预处理 + PaddleOCR + 阅读顺序 + PP-Structure |
| `tools/dataproc/schema.py` | 新 | `ProductRecord` / `CorpusRecord` / `HQProductRecord` 工具自管 schema（零反向依赖） |
| `tools/dataproc/cli.py` | 新 | `dataproc build/crawl/ocr` CLI；独立 `dataproc/config.yaml`（含工具自身 LLM 配置） |
| `deploy/dependency-manifest.yaml` | 改(增) | OCR Tier1 条目（paddlepaddle/paddleocr/PP-Structure 权重 URL+校验和） |
| `harness/fixtures/` | 新 | 自带小样例（数字 PDF / 扫描件 PDF / 含表图片，均 < 数百 KB） |
| `harness/test_dataproc_pdf.py` | 新 | `@module ingest`：I7 数字直抽 / I8 扫描件 OCR / I9 表格结构 / I11 缺依赖报错 |
| `harness/test_dataproc_ocr.py` | 新 | `@module ingest`：I10 图片 OCR / I14 长图切片 / I15 预处理提升 / I16 无文字不编造；`RUN_REAL_OCR=1` 门控 |

> 状态：**planned（P2，工具侧）**。落地后 `02-index.md` MOD-knowledge-ingest 升级、G1 进展；`test_dataproc_*` 由 ⏸ deferred 转已落地。

### P2 harness 验收表（计划，`harness/test_dataproc_pdf.py` + `test_dataproc_ocr.py`，`@module ingest`）
| 编号 | 断言 | 对应实现 | 门控 |
|------|------|----------|------|
| I7 | 数字 PDF 经 `pypdf` 抽出非空文本，`is_scanned=False` | `pdf.PDFAdapter` | 默认 |
| I8 | 扫描件 PDF（无文本层）经 PaddleOCR 产出文本，`is_scanned=True` | `pdf.PDFAdapter` | `RUN_REAL_OCR=1` |
| I9 | 含表格 PDF 经 PP-Structure 抽 `table_cells` 结构 | `pdf.PDFAdapter` | `RUN_REAL_OCR=1` |
| I10 | 产品图/规格表经 `ImageTableAdapter` OCR 出文本/表格 | `image_table.ImageTableAdapter` | `RUN_REAL_OCR=1` |
| I11 | 缺 OCR 依赖时适配器显式报错（不静默/不崩全局） | `adapters` | 默认 |
| I13 | `ocr_enabled=false` 实例不触发 OCR 路径、不依赖 OCR 权重 | `config`+`manifest` | 默认 |
| I14 | 电商详情页长图经纵向切片后完整 OCR，无截断丢失 | `image_table` | `RUN_REAL_OCR=1` |
| I15 | 噪声/暗光/畸变实拍图经预处理后 OCR 出文本（对比无预处理有提升） | `image_table` | `RUN_REAL_OCR=1` |
| I16 | 无文字照片 → OCR 出空且不编造、标 `low_conf`、不崩 | `image_table` | `RUN_REAL_OCR=1` |

---

## 〇·产物契约（Bundle Contract）—— 隔离边界本身

> 工具与 agent **唯一共享的契约**是产物 bundle 的 schema（语言中立 NDJSON + manifest）。
> 工具不 import agent；agent 不 import 工具；双方各自独立演化，仅受此契约约束。

**Bundle 目录布局**（`dataproc build --out <bundle_dir>` 产出）：
```
<bundle_dir>/
├── manifest.json        # 绑定企业/工具版本/校验和/计数/时间
├── products.ndjson      # 结构化产品（每企业隔离，agent 端→ products_milk/nutrition）
├── corpus.ndjson        # 非结构化 RAG 文本（agent 端→ corpus：b_kb / hq_kb）
└── hq_products.ndjson   # HQ 商品库种子（厂商侧，onboarding 播种用）
```

**`manifest.json`**：
```json
{
  "schema_version": "1.0",
  "enterprise_id": "ent_b",
  "tool_version": "dataproc 0.1.0",
  "generated_at": "2026-07-20T12:00:00+08:00",
  "counts": {"products": 12, "corpus": 80, "hq_products": 3},
  "checksums": {"products.ndjson": "<sha256>", "corpus.ndjson": "<sha256>", "hq_products.ndjson": "<sha256>"},
  "structuring_provider": "ollama://qwen2.5:latest"
}
```
> `enterprise_id` 硬性绑定：importer 拒绝加载 `enterprise_id != 运行实例` 的 bundle（企业隔离兜底）。`structuring_provider` 仅作审计记录，**不含凭据**。

**`products.ndjson`**（每行一个 JSON 对象）：
```json
{
  "kind": "milk",
  "uid": "reg:国食注字YP20180012",
  "status": "confirmed",
  "source_ref": "pdfs/ruihu_1duan.pdf",
  "resolved": {"match": "reg_number", "reg_number": "国食注字YP20180012", "key": ["brand","name","stage"]},
  "fields": {"name":"睿护婴儿配方奶粉1段","brand":"贝贝优","stage":"1段","age_range":"0-6个月",
             "price":368.0,"origin":"中国","milk_origin":"新西兰","ptype":"牛奶粉",
             "reg_number":"国食注字YP20180012","manufacturer":"贝贝优营养品有限公司",
             "ingredients":"...","nutrition":"...","highlights":"..."}
}
```
> `uid` = 稳定产品键（有 `reg_number` 用 `reg:<号>`，否则 `tuple:<sha1(brand|name|stage)>`），用于 `corpus.ndjson` 的 `product_uid` 关联溯源。`status` ∈ {`confirmed`, `pending`}（实体解析结果，见 P3）。`fields` 为 `MilkProduct`/`NutritionProduct` 字段（**不含** `enterprise_id`/`id`，由 importer 补）。

**`corpus.ndjson`**（每行一个 JSON 对象）：
```json
{
  "part": "b_kb",
  "title": "睿护1段 电商详情页 OCR",
  "content": "（原始 OCR 文本/网页正文，保留溯源）",
  "product_uid": "reg:国食注字YP20180012",
  "meta": {"source": "ocr", "page": 2, "lang": "zh"},
  "lang": "zh"
}
```
> `part` ∈ {`b_kb`（企业自有 RAG 文本）, `hq_kb`（跨企业共享）}。`product_uid` 可选，关联 `products.ndjson` 的 `uid`（importer 解析为 `product_id`）。
> 注：结构化产品的**语义分块**（基础信息/配料表/营养成分）由 agent 端 `store.add_milk` 的 `to_chunks()` 自动生成（源自 `fields`），**不在产物里重复**；`corpus.ndjson` 仅承载工具特有的原始文本（OCR 详情页/网页/PDF 正文），二者文本不同、不重复。

**`hq_products.ndjson`**（每行一个 JSON 对象）：
```json
{"kind":"milk","fields":{"name":"...","brand":"...","reg_number":"...",...},"meta":{"vendor":"..."}}
```

---

## 〇·P3、本次实施（结构化抽取 + 商品实体解析与分类 —— 落在 `tools/dataproc/`，闭合 Q-DB1）

> 分类：**P4 扩展**。承接 P2 的 OCR/爬取输出，补全「认出是哪个商品 + 归到哪类」的桥——即 PRD 待确认项 **Q-DB1**。
> **全部落在工具侧 `tools/dataproc/`**，产出物写入产物契约（上节），**不直接写 agent 库**。
> 决策（已确认）：主键 = `reg_number` 优先 + `(brand,name,stage)` 元组兜底；新商品 `pending` 建档待确认（复用 `resolve_and_archive` 安全网思路）；分类 = `ptype` 推断 + `product_category` 取自企业 `conf.yaml`。

### 意图（must-have）
1. **结构化抽取（structuring）**：P2 产出文本 → 经工具**自带 LLM provider** 抽取成 `MilkProduct` 形状 `fields`（锚定原文、不编造，沿用"禁止 LLM 改写事实"非目标）。复用 `resolve_and_extract` 的抽取模式（已验证）。
2. **商品实体解析（entity resolution）**：抽取 `fields` → 与企业已知商品目录（`dataproc` 自管的 known list，来自上次 bundle 或企业提供的目录）匹配：
   - **主键**：`reg_number` 优先；无注册号品类用 `(brand,name,stage)` 元组兜底。
   - **先 HQ 商品库比对**（跨企业已知，data-model.md:13/127），命中则复用结构化字段、只补企业独有；未命中再落企业 B-end 独有。
   - 命中 → `status=confirmed` 原地更新；未命中 → `status=pending` 新建（防误建/污染）。复用 baby `resolve_and_archive` 安全网（pending 待确认、精确优先、防跨实体误并）。
3. **分类（classification）**：`ptype` 由字段推断；`product_category` 取自企业 `conf.yaml` 产品结构，打标供 KB 过滤/溯源。
4. **落产物**：结构化产品写 `products.ndjson`；原始 OCR 文本仍写 `corpus.ndjson`（带 `product_uid` 关联溯源）；HQ 复用写 `hq_products.ndjson`；三者经 `uid`/`product_uid` 关联。
5. 每行为配 harness（CVC 只增不删）。

### LLM 调用机制（structuring —— 工具自带，与 agent 解耦）
P3 的"结构化抽取"通过 **工具自身的 LLM provider 抽象**（`tools/dataproc/llms/`）完成，**完全独立于 agent 的 `LLMProvider`**：
- **工具自带 provider + 独立配置**：`tools/dataproc/config.yaml` 的 `llm:` 段（kind/base_url/model/api_key），与 agent 的 `conf.yaml` **互不可见、不共享凭据**。工具不 `import src.agent`、不 `import src.kb`。
- **批量离线**：工具是批量任务，单文档/单图 = 1 次 `provider.complete`，延迟/额度可接受。
- **Prompt 结构**：稳定前缀（抽取指令 + 企业品类 schema，`cache_control=True` 断点）→ 变量区（本资料文本）→ JSON 输出对齐 `fields`；`temperature=0` 保确定性。
- **混合 + 兜底**：规则抽取（正则抓段位/净含量/品牌）作主/兜底，LLM 抽其余；JSON 解析失败 → 退规则 + 标 `low_conf`/`parse_failed`，**不编造**。
- **硬性约束（彻底隔离）**：工具对 agent 的**唯一**依赖是产物契约（上节 NDJSON schema）；不引用 `src.*` 任何模块；工具可整目录拷贝到任意设备独立 `pip install` 运行。

### 非目标（non-goals，本次不做）
- **不改 OCR 机制**（P2 已定）；本 P3 只消费 P2 输出。
- **不自动删除 pending / 不自动合并**（pending 由企业员工在 agent 端显式确认/合并/删除）。
- **不依赖人工在源里写死结构**：markdown 商品仍可由工具内 `MarkdownProductAdapter` 显式路径解析（迁入工具后）。
- 不做跨企业数据互通（除 HQ 商品库播种这一既定机制外）；企业 B-end 严格隔离。

### 文件与 harness 落点
| 文件 | 动作 | 说明 |
|------|------|------|
| `tools/dataproc/structurer.py` | 新 | structuring（工具自带 provider）+ 规则兜底 |
| `tools/dataproc/resolver.py` | 新 | entity resolution（reg_number/元组/HQ/new→pending）+ uid 生成 |
| `tools/dataproc/classifier.py` | 新 | `ptype` 推断 + `product_category` 取自 conf.yaml |
| `tools/dataproc/llms/__init__.py` | 新 | 工具自带 provider 抽象（独立 config，零反向依赖） |
| `tools/dataproc/cli.py` | 改 | `dataproc build` 串起 crawl→ocr→structure→resolve→classify→写 bundle |
| `harness/test_dataproc_resolver.py` | 新 | `@module ingest`：RES1 抽取 / RES2 reg_number 更新 / RES3 元组兜底 / RES4 新建 pending / RES5 HQ 复用 / RES6 分类 / RES7 防跨实体误并 |

> 状态：**planned（P3，工具侧）**。依赖 P2 落地；落地后 Q-DB1 由「待确认」转「已定」，`02-index.md` 升级、G1 进展。

### P3 harness 验收表（计划，`harness/test_dataproc_resolver.py`，`@module ingest`）
| 编号 | 断言 | 对应实现 | 门控 |
|------|------|----------|------|
| RES1 | OCR 文本经抽取产出 `MilkProduct` 形状 `fields`，锚定原文不编造 | `structurer` | 默认（mock/轻量抽取） |
| RES2 | 抽出 `reg_number` 命中已有商品 → `status=confirmed` 原地更新，不新建 | `resolver` | 默认 |
| RES3 | 无 `reg_number`（营养品）经 `(brand,name,stage)` 元组匹配 | `resolver` | 默认 |
| RES4 | 未命中任何键 → `status=pending` 新建（不污染 confirmed） | `resolver` | 默认 |
| RES5 | 抽出门店共有的 HQ 商品 → 复用 HQ 结构化字段、只补企业独有 | `resolver` + HQ | 默认（mock HQ） |
| RES6 | `ptype` 由字段推断 + `product_category` 取自 conf.yaml | `classifier` | 默认 |
| RES7 | 同名不同主键不误并（防跨实体污染，同 baby P10） | `resolver` | 默认 |

---

## 〇·P4、本次实施（agent 端导入器 —— `src/ingest/importer.py` 加载产物，隔离边界的 agent 侧落点）

> 分类：**P4 扩展**（agent 侧）。这是「工具 ↔ agent」隔离边界的 agent 半边：把 `tools/dataproc/` 产出的
> bundle 载入 `store`（含向量化/索引）。agent 端**只做搬运 + 向量化，不做 OCR/结构化/解析，不调用 LLM**。

### 意图（must-have）
1. **`load_bundle(bundle_dir, store, enterprise_id)`**：
   - 校验 `manifest.json`（`schema_version`、校验和、`enterprise_id == 运行实例` 否则拒绝）；
   - 读 `products.ndjson` → 由 `fields` 构建 `MilkProduct`/`NutritionProduct`（补 `enterprise_id`）→ `store.add_milk`/`add_nutrition`；记录 `uid→product_id` 映射；
   - 读 `corpus.ndjson` → 解析 `product_uid→product_id` → `store.add_knowledge`（part=b_kb）或 `store.add_hq_knowledge`（part=hq_kb），`meta` 带 `product_id` 关联与溯源标签；
   - 读 `hq_products.ndjson` → onboarding 播种（HQ 商品表 + HQ 共享库）。
2. **幂等**：复用 `ingest_dedup` 内容哈希去重，重载同一 bundle 安全（不重复入库、不重复向量）。
3. **企业隔离**：`manifest.enterprise_id` 必须 == 运行实例 `enterprise_id`，否则拒绝加载（隔离兜底）。
4. 每行为配 harness（CVC 只增不删）。

### 非目标（non-goals）
- **不做 OCR / 结构化抽取 / 实体解析 / 分类**（那是 `tools/dataproc/` 职责，见 P2/P3）。
- **不调用 LLM**（importer 是纯 IO + 向量化）；**不 import 工具**（仅消费产物文件）。
- 不改既有 `store` 检索/生产闭环（只读桥接 `retrieve` 做集成断言）。

### 文件与 harness 落点
| 文件 | 动作 | 说明 |
|------|------|------|
| `src/ingest/importer.py` | 新 | `load_bundle`：manifest 校验 + products/corpus/hq 载入 + uid→pid 映射 + 幂等 |
| `src/ingest/protocol.py` | 保留 | `KnowledgeRecord` / `SeedAdapter` 仍服务 in-repo seed/演示路径（与 bundle 契约并存） |
| `harness/test_importer.py` | 新 | `@module ingest`：IMP1 载入 products_milk / IMP2 corpus 可检索 / IMP3 HQ 播种 / IMP4 跨企业拒绝 / IMP5 幂等重载 / IMP6 边界集成（工具产 bundle → importer → store → retrieve 命中） |

> 状态：**planned（P4，agent 侧）**。与 P2/P3 同批落地；落地后 MOD-knowledge-ingest 升级、G1 进展。

### P4 harness 验收表（计划，`harness/test_importer.py`，`@module ingest`）
| 编号 | 断言 | 对应实现 | 门控 |
|------|------|----------|------|
| IMP1 | bundle 的 `products.ndjson` 经 importer 载入 `products_milk`/`products_nutrition` | `importer.load_bundle` | 默认 |
| IMP2 | bundle 的 `corpus.ndjson` 载入后 `retrieve` 可命中（向量+FTS） | `importer`+`store.retrieve` | 默认 |
| IMP3 | `hq_products.ndjson` 播种进 HQ 商品表 + HQ 共享库 | `importer` | 默认 |
| IMP4 | `manifest.enterprise_id != 运行实例` → importer 拒绝加载（企业隔离） | `importer` | 默认 |
| IMP5 | 同 bundle 重载二次 → 入库计数为 0（幂等去重） | `importer`+`ingest_dedup` | 默认 |
| IMP6 | 边界集成：工具产 bundle → importer → store → 该商品 `retrieve` 命中其结构化块+原始 OCR 块 | `dataproc`+`importer` | 默认（fixture 源） |

---

## 〇·迁移说明（P1 in-process 适配器的去向）

> P1 已绿的 `WebCrawlerAdapter` / `MarkdownProductAdapter` / `IngestPipeline`（现位于 `src/ingest/`）在落地 P2/P3 时
> **迁入 `tools/dataproc/`**，作为工具的「in-repo 批量源适配器」（crawl / markdown 商品 → 归一为产物契约）。
> 迁移时 `harness/test_ingest.py`（I1–I6）一并迁移/改写为 `harness/test_dataproc_*.py`，确保 **20/20 gate 持续全绿**；
> 边界集成测试 **IMP6** 横跨「工具产 bundle → agent importer → store → retrieve 命中」两上下文，作为隔离正确性的核心证据。
> `src/ingest/` 落地后仅保留：`protocol.py`（seed/演示路径）+ `importer.py`（产物导入）+ `SeedAdapter`（onboarding）。

---

## 一、采集适配器（三类来源，C3）—— 均位于 standalone 工具 `tools/dataproc/`

> 下表适配器实现于 `tools/dataproc/adapters/`，产出归一为**产物契约**（见「〇·产物契约」），**不在本模块（agent 端）直接写库**。

| 适配器（工具侧） | 工具 | 输入 | 产出（→ bundle） |
|--------|------|------|------|
| `WebCrawlerAdapter` | 标准库 `urllib`+`html.parser`（零依赖） | 官网 URL/域名（商品页/知识页/FAQ） | `CorpusRecord`（part=b_kb） |
| `PDFAdapter` | 数字 PDF 走 `pypdf` 直抽；扫描件/表格走 PaddleOCR + PP-Structure | PDF 文件 | `CorpusRecord`（含扫描 OCR / 表格结构） |
| `ImageTableAdapter` | PaddleOCR + 表格识别 + opencv 预处理 | 产品图 / 规格表图片 / 电商长图 | `CorpusRecord`（文本 + 表格结构） |
| `MarkdownProductAdapter` | 正则解析 frontmatter+表格 | 商品 markdown | `ProductRecord`（结构化字段） |

- **去重**：网页按 URL/域名；文件按内容哈希；避免重复入库（落在工具内 known list / bundle 级）。
- **企业产品结构挂钩**：每条 `ProductRecord` 打 `product_category`（来自企业 `conf.yaml` 的产品结构），供 kb 过滤/溯源。
- **agent 端对应物**：`src/ingest/importer.py` 把产物契约载入 `store`（见「〇·P4」）；`src/ingest/protocol.py` 的 `KnowledgeRecord`/`SeedAdapter` 保留服务 in-repo seed/演示路径。

---

## 二、统一接口（归一为同一结构）

**工具侧（`tools/dataproc/`）**：归一为工具自管 schema `ProductRecord` / `CorpusRecord` / `HQProductRecord`（`dataproc/schema.py`），
经 `dataproc build` 落盘为产物契约（NDJSON bundle）。`IngestPipeline` / `WebCrawlerAdapter` / `MarkdownProductAdapter` 在落地 P2/P3 时迁入工具（见「〇·迁移说明」）。

**agent 侧（`src/ingest/`）**：`KnowledgeRecord` / `SeedAdapter`（`protocol.py`）保留服务 in-repo seed/演示路径；
新增 `importer.load_bundle(bundle_dir, store, enterprise_id)` 作为**唯一**把产物写入 `store` 的入口（见「〇·P4」）。

---

## 三、容错与续传
- **失败重试**：单条失败按指数退避重试；仍失败则记录到失败清单，供补采。
- **断点续传**：批量采集记录进度（已处理 URL/文件哈希），中断后可续，不重头。
- **不静默丢弃**：任何未入库来源都留痕（失败清单 + 原因），不谎称成功。

---

## 四、对外契约 / 接口（自研）
- `IngestPipeline.configure(enterprise_id, sources_cfg)`：按企业配置加载来源（URL 列表/文档目录/爬取深度）。
- `IngestPipeline.run(source) -> int`：执行采集并写入 MOD-kb，返回新增条数。
- `IngestPipeline.recollect(failures)`：对失败清单补采。
- `UnifiedKnowledgeSource.fetch() -> list[KnowledgeRecord]`（各适配器实现）。

---

## 五、实现步骤
1. **适配器**：`WebCrawlerAdapter`（crawl4ai）/ `PDFAdapter`（MinerU + PaddleOCR）/ `ImageTableAdapter`（PaddleOCR + 表格）。
2. **归一**：各适配器产出统一 `KnowledgeRecord`（带 `source_type`/元数据/`product_category`）。
3. **管线**：`configure` → `run` → 分块 → 去重 → 写 MOD-kb（调用 `KB.for_enterprise(eid).add()`）。
4. **容错**：重试 + 断点续传 + 失败清单。
5. **配置**：每企业 `conf.yaml` 的 `ingest` 段（来源类型/URL/目录/深度/产品类目映射）。

---

## 六、关键风险与缓解
| 风险 | 缓解 |
|------|------|
| 母婴参数歧义 | OCR 后保留原始字段/单位上下文，避免静默纠错 |
| 版权/合规 | 仅采集企业自有或已授权内容；遵守 robots |
| 来源不可信 | 入库前标注来源与采集时间，不冒充「已验证知识」 |
| 单点失败拖垮整批 | 单条失败隔离 + 失败清单 + 续传 |
| 重复入库 | URL/内容哈希去重 |
| 大模型幻觉入知识 | 采集是「搬运」非「生成」，禁止用 LLM 改写事实性内容 |

---

## 七、harness 验收（真实运行，非自述）
> 用本地样例（**本地 stub HTTP 服务**驱动真实爬虫客户端代码 / 样例 markdown 商品 / fixture PDF·图片）断言各适配器与归一/容错/隔离。
> P1 落在 `harness/test_ingest.py`（`@module ingest`）；P2/P3 落在 `harness/test_dataproc_*.py`；P4 落在 `harness/test_importer.py`。

| 编号 | 断言 | 对应 PRD | 状态 |
|------|------|----------|------|
| I1 | `WebCrawlerAdapter` 打本地 stub 服务产出非空 `CorpusRecord`（part=b_kb） | `test_ingest_crawler` | ✅ P1 落地（迁移工具后改写 `test_dataproc_crawler`） |
| I2 | `MarkdownProductAdapter` 产出 `ProductRecord`（结构化字段） | （markdown 适配器） | ✅ P1 落地（迁移工具） |
| I3 | 多源归一为同一产物结构 | `test_ingest_unified` | ✅ P1 落地（迁移工具） |
| I4 | 跨运行内容哈希去重：同页二次入库计数为 0 | `test_ingest_dedup` | ✅ P1 落地（持久化 `ingest_dedup` 表） |
| I5 | 单适配器抛错不中断整批、失败留痕 | `test_ingest_resilient` | ✅ P1 落地 |
| I6 | 集成：markdown → 产物 → importer → store，产品落 `products_milk` 且 retrieve 命中 | （桥接 MOD-kb + importer） | ✅ P1 落地（迁移后以 IMP6 强化） |
| I7–I16 | PDF / OCR 适配器（**决策已定，见「〇·P2」**：PaddleOCR + PP-Structure / 端侧可选安装 / `RUN_REAL_OCR=1` 门控） | `test_dataproc_pdf` / `test_dataproc_ocr` | ⏸ 计划 P2（落地后转已落地） |
| RES1–RES7 | 结构化抽取 + 实体解析 + 分类（**见「〇·P3」**，工具侧） | `test_dataproc_resolver` | ⏸ 计划 P3 |
| IMP1–IMP6 | agent 端导入器加载 bundle → store → retrieve 命中（**见「〇·P4」**，隔离边界） | `test_importer` | ⏸ 计划 P4 |

---

## 八、注意事项 / 雷区
- 母婴商品参数易含单位/成分歧义，OCR 后需保留原始字段，避免静默纠错。
- 外部站点需遵守 robots 与版权，仅采集企业自有或已授权内容。
- 不得把采集到的原文直接当「已验证知识」——入库前标注来源与采集时间。
- 首版**不实现**结构化 API 适配器；若后续需要，作为新适配器扩充，不动核心与知识库逻辑。
- 本模块完全自研（方案 B），不 import Hermes。
