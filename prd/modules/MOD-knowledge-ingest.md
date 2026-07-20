# MOD-knowledge-ingest 模块详解（知识转化与采集层）

> 依据 charter C3 / C5：知识来源 = **PDF/说明书 + 图片表格 + 爬虫（官网网页）**，均为非结构化，
> **首版不含结构化 API 适配器**。配套**知识转化工具链**（爬虫 / OCR / 多源统一接口）把散落知识归一后
> 写入 MOD-kb。**方案 B 完全自研**（不依赖 Hermes）。本文件为**可实现规格**。

## 职责
把企业散落的非结构化知识（说明书 PDF、产品图、规格表格、官网商品/知识页）转化为结构化知识记录，
经**统一适配接口**归一后写入知识库（MOD-kb）。对本模块而言，下游知识库是黑盒契约。

> **B 端结构化产品**：奶粉/营养品等可能需从产品册 PDF/图片**抽取结构化字段**填入
> `prd/references/data-model.md` 定义的品类 schema（如奶粉 14 必填字段）。**字段来源与入库方式见
> Q-DB1，待确认**——可能是解析抽取、Excel 导入或对接 ERP，不同企业可能不同。

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

## 一、采集适配器（三类来源，C3）
| 适配器 | 工具 | 输入 | 产出 |
|--------|------|------|------|
| `WebCrawlerAdapter` | crawl4ai | 官网 URL/域名（商品页/知识页/FAQ） | 网页文本分块 |
| `PDFAdapter` | MinerU（版式 PDF）+ PaddleOCR（扫描件） | PDF 文件 | 结构化文本（含扫描 OCR） |
| `ImageTableAdapter` | PaddleOCR + 表格识别 | 产品图 / 规格表图片 | 文本 + 表格结构 |

- **去重**：网页按 URL/域名；文件按内容哈希；避免重复入库。
- **企业产品结构挂钩**：每条记录打 `product_category`（来自企业 `conf.yaml` 的产品结构），供 kb 过滤/溯源。

---

## 二、统一接口（归一为同一结构）
- `UnifiedKnowledgeSource`（Protocol）：每个采集源实现 `fetch() -> list[KnowledgeRecord]`。
- `KnowledgeRecord` 结构：`{ source_type, title, content, metadata, lang }`。
- `IngestPipeline.run(source) -> int`：返回成功入库的记录数。
- `IngestPipeline.register(source)`：注册新适配器（开闭原则，新增来源不加改核心）。

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
> 用本地样例（**本地 stub HTTP 服务**驱动真实爬虫客户端代码 / 样例 markdown 商品）断言各适配器与归一/容错。统一落在 `harness/test_ingest.py`（`@module ingest`）。

| 编号 | 断言 | 对应 PRD 草案 | 状态 |
|------|------|---------------|------|
| I1 | `WebCrawlerAdapter` 打本地 stub 服务产出非空 `source_type=web` `KnowledgeRecord` | `test_ingest_crawler` | ✅ P1 落地 |
| I2 | `MarkdownProductAdapter` 产出 `source_type=milk` 且 `structured` 持有 `MilkProduct` | （markdown 适配器） | ✅ P1 落地 |
| I3 | 多源（web/markdown）归一为同一 `KnowledgeRecord` 结构 | `test_ingest_unified` | ✅ P1 落地 |
| I4 | `IngestPipeline` 跨运行内容哈希去重：同页二次入库计数为 0 | `test_ingest_dedup` | ✅ P1 落地（持久化 `ingest_dedup` 表） |
| I5 | 单适配器抛错不中断整批、失败留痕、兄弟适配器仍入库 | `test_ingest_resilient` | ✅ P1 落地 |
| I6 | 集成：markdown 走管线入真实 store，产品落 `products_milk` 且可被 `retrieve` 命中 | （桥接 MOD-kb） | ✅ P1 落地 |
| — | PDF / OCR 适配器（crawl4ai / MinerU / PaddleOCR） | `test_ingest_pdf` / `test_ingest_ocr` | ⏸ deferred（非 P1 目标，仍 `NotImplementedError`） |

---

## 八、注意事项 / 雷区
- 母婴商品参数易含单位/成分歧义，OCR 后需保留原始字段，避免静默纠错。
- 外部站点需遵守 robots 与版权，仅采集企业自有或已授权内容。
- 不得把采集到的原文直接当「已验证知识」——入库前标注来源与采集时间。
- 首版**不实现**结构化 API 适配器；若后续需要，作为新适配器扩充，不动核心与知识库逻辑。
- 本模块完全自研（方案 B），不 import Hermes。
