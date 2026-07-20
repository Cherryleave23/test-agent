# MOD-kb 模块详解（企业定制知识库）

> 依据 charter C1 / O1 / C3 / C5：为**单家企业**维护可检索知识库；按企业产品结构做分块与嵌入，
> 提供向量检索，并**企业间严格隔离**。向量库 = **Chroma（O1，嵌入式 PersistentClient，2026-07-20 由 SQLite-vec 改换）**，
> **方案 B 完全自研**（不依赖 Hermes）。本文件为**可实现规格**。

## 职责
端侧数据库由**两部分**组成（详见 `prd/references/data-model.md`）：
- **总部知识库 HQ KB**（共享）：所有实例包含的育儿知识 + 营养成分解析，跨企业共用、只读。
- **B 端数据库 B-end DB**（每企业不同）：结构化产品目录（奶粉/营养品/尿不湿/服务），按品类 schema。
本模块负责两部分的存储、嵌入与检索，并保证 **B-end 每企业隔离**（HQ 本就共享）。

---

## 一、存储设计（Chroma + SQLite，与 O1 一致）
- **向量 = Chroma 嵌入式 `PersistentClient`**（每实例一个持久化目录 = 物理企业隔离）；原生 metadata
  过滤按 `enterprise_id` 强化隔离。HQ 共享库以 `enterprise_id='hq'` 标记，检索时 `$or` 同时命中本企业 + HQ。
- **结构化产品表 + 会话**仍存 **SQLite**（同实例库，或独立 `kb.db`/`sessions.db`，按部署定）。
- **两部分存储**：
  - **HQ KB（共享）**：随产品分发、实例内只读；Chroma 中 `enterprise_id='hq'`，SQLite corpus 表 `enterprise_id IS NULL`。
  - **B-end DB（每企业结构化）**：每企业**结构化产品表**，如
    `products_milk(id, enterprise_id, name, brand, stage, age_range, price, origin, milk_origin,
    ptype, reg_number, manufacturer, ingredients, nutrition, highlights)`，
    精确字段查询 + 语义检索混合。品类 schema 见 `prd/references/data-model.md`。
- **混合检索（RRF 融合）**：
  - 向量召回：Chroma `query_embeddings` + metadata 过滤（`enterprise_id`）→ 近邻 id + distance。
  - 关键词召回：SQLite FTS5 对 `corpus` 表全文检索 → id + rank。
  - **FTS5 中文分词（关键决策）**：SQLite FTS5 默认 `unicode61` 分词器**不按字切分 CJK**
    （连续中文被当作一整个 token），导致"奶粉/营养品"等复合词无法整体命中。本实现显式把
    索引文本与查询都拆成「中文单字 + 英文/数字词 + 母婴复合词（`_category_terms`）」并以空格分隔，
    使 `unicode61` 把它们各自视为独立 token，从而实现字级重叠召回；查询端（`_fts_query`）与
    索引端（`fts_text`）共用同一套 `_fts_tokenize` 逻辑。复合词整体 token 兼顾精度，单字 token 保证召回。
  - 两者 RRF 融合后回查 `corpus` 取正文与 structured meta；再叠加 `WHERE enterprise_id` 防御纵深。
  B-end 结构化表额外支持精确字段过滤（`WHERE stage=? AND ptype=?`）。
  - **嵌入模型（`embed()` 按 kind 调度）**：`mock`（确定性词袋，harness 默认，无外部依赖）/
    `bge`（`BAAI/bge-small-zh-v1.5`，512 维，端侧 CPU 可跑，真实语义）。维度随模型变化
    （`EMBED_DIM`），Chroma 按集合自适应。**相关性门控随模型**：mock L2≤1.45；bge 归一化向量
    实测在域查询 L2≈0.81~0.87、跨域 L2≈1.17+，取 **1.05** 干净分离防幻觉。
  - **独立重排器（reranker，与召回解耦的精度阶段）**：召回（RRF 融合）只负责「广度」产出候选集，
    重排负责「精度」——`src/common/rerank.py` 用 **cross-encoder 对 (query, doc) 逐对打分**再排序。
    设计上**重排与双塔向量召回完全解耦**：召回代码不动，重排器可插拔替换。
    - `NoReranker`（`kind="none"`，默认）：不加载模型、返回等长 1.0，调用方退回召回分数排序——
      **mock / 轻量场景与现有业务零影响**（满足「不影响业务」约束）。
    - `BgeReranker`（`kind="bge-reranker-v2-m3"`）：复用已装 `sentence_transformers.CrossEncoder`
      加载 **BAAI/bge-reranker-v2-m3**（开源多语言 cross-encoder，原生中文，母婴垂域精度显著优于
      手写静态权重启发式），**不重复造轮子**、零新重依赖。惰性加载 + 单例复用。
    - 重排在**分块粒度**进行（cross-encoder 对所有候选块打分），再按产品取最高分块为代表，
      根治「合并阶段每产品只留一个召回最佳块导致相关块被丢弃」的精度损失（见 R7）。
    - `KnowledgeStore(rerank_kind=...)` 经 `common.config.RerankConfig` 配置；`scripts/ingest_bend.py`
      提供 `--rerank` 开关。
  - **B 端商品入库适配器**：`ingest/markdown_product.py` 把爬虫/手工导出的商品 markdown
    （YAML frontmatter + 正文表格，含脏数据如 trailing 全角逗号、`400g，g` artifacts）解析清洗为
    `MilkProduct` 14 字段；配套 CLI `scripts/ingest_bend.py` 落地「统一多源接口」入库。

---

## 二、分块与嵌入
- **分块**：按语义/标题切分，母婴长说明书避免生硬截断导致参数错位；每块带 `product_category`/来源。
- **嵌入**：可配置中文 embedding 模型（默认 **bge-small-zh / 本地**）；向量维度 `<dim>` 与模型绑定。
- **模型版本**：记录 embedding 模型版本；**更换模型必须重建索引**（旧向量维度/语义不兼容，禁止混用）。

---

## 三、隔离（红线）
- **每实例单 SQLite 文件** = 物理隔离；检索再叠加 `WHERE enterprise_id = ?` 防御纵深。
- 即使同进程服务多企业，缺 `enterprise_id` 过滤即视为缺陷（harness 必测）。
- 元数据保留来源/采集时间/产品类目，供溯源与过滤。

---

## 四、对外契约 / 接口（自研）
- `KB.for_enterprise(enterprise_id) -> EnterpriseKB`：获取该企业隔离的知识库句柄。
- `EnterpriseKB.add(records: list[KnowledgeRecord]) -> int`：写入并返回新增条数。
- `EnterpriseKB.search(query, top_k, hybrid=False) -> list[RetrievalHit]`：返回带分数与出处的命中。
- `RetrievalHit` 结构：`{ content, score, source, metadata }`。

---

## 五、实现步骤
1. **schema**：建 `vec0` 向量表 + 元数据列 + `enterprise_id` 索引；可选 FTS5 表。
2. **分块器**：语义/标题切分，产出 `KnowledgeRecord`（来自 MOD-ingest）。
3. **嵌入器**：可配置模型，批量生成向量；记录模型版本。
4. **写入**：`add()` 落库（向量 + 元数据），返回条数。
5. **检索**：`search()` 做 ANN + `enterprise_id` 过滤；`hybrid=True` 走 FTS5+vec0+RRF。
6. **隔离校验**：所有读写路径强制 `enterprise_id`；缺字段即报错。

---

## 六、关键风险与缓解
| 风险 | 缓解 |
|------|------|
| 跨企业泄露（合规红线） | 单实例单库 + `WHERE enterprise_id` 防御纵深；harness 必测隔离 |
| 嵌入模型混用 | 记录模型版本；换模型重建索引，禁止旧向量复用 |
| 母婴参数歧义 | 分块保留产品类目/单位上下文，避免静默纠错 |
| 混合检索质量 | RRF 融合权重可调；向量为主、关键词兜底 |
| 规模上限 | Chroma 百万~千万级顺畅；极端边缘资源受限可回退 SQLite-vec |
| 索引损坏 | 写入原子 + 定期校验；Chroma 目录可整体重建 |

---

## 七、harness 验收草案（真实运行，非自述）
> 用临时 Chroma 目录 + SQLite 文件，预置多企业样本，断言写入/检索/隔离/溯源/召回。

- `test_kb_add_search.py`：写入后可被检索命中。
- `test_kb_isolation.py`：不同企业知识互不串（缺 `enterprise_id` 过滤即 FAIL）。
- `test_kb_provenance.py`：检索返回带出处（source/product_category/collected_at）。
- `test_kb_recall.py`：同义/近义查询能召回（hybrid 开时更稳）。
- `test_kb_model_version.py`：换嵌入模型后旧索引被拒绝/重建，不混用。
- `test_kb_hybrid.py`：`hybrid=True` 时规格参数类精确查询召回优于纯向量。
- `test_reranker.py`（@module reranker，**已落地**）：独立重排器专属验收——
  RR1 透传（none 不加载模型、mock 业务零影响）、RR2 工厂契约（fail-closed 未知 kind 报错）、
  RR3 真实重排（BAAI/bge-reranker-v2-m3 按语义相关性重排，跨域无关文档被压底）、
  RR4 解耦集成（同套检索代码换 rerank_kind 仍正确检索、企业隔离成立）。

---

## 八、注意事项 / 雷区
- **绝不可**在检索时漏掉 `enterprise_id` 过滤——跨企业泄露是合规事故。
- 嵌入模型更换后，旧向量需重建索引（记录模型版本，避免混用）。
- 母婴健康类内容检索可配置「敏感度过滤」钩子（见 MOD-agent 免责边界）。
- 本模块完全自研（方案 B），不 import Hermes。

---

## 九、已知局限与规模待办（deferred，暂不实现）

- **规模评测集（500+ 产品 benchmark）**：当前 harness 是「**验收（acceptance）**」性质（5 产品、证"没坏"），
  **不是**「规模质量评测（benchmark）」。部署端侧可能达 500+ 产品时，5 产品 eval 无法证明"在 500 个里排得准/快"。
  **状态：已记录，暂不做**——原因：当前无该数据量，且真实企业目录尚未接入（见 MOD-knowledge-ingest）。
  - 待实现时需补一套**独立** benchmark（不与验收 harness 混）：合成 500+ 产品目录（含兄弟近似品作 hard negative）、
    graded 查询集（gold answer）、输出 `recall@5` / `MRR` / 重排器精度 / `OOV` 幻觉率 / 单查询延迟。
  - 现有 5 产品验收 harness **保留且只增不删**（controlled-vibe-coding）。
  - 500+ 时才显著的风险：候选池稀释、近似重复兄弟品（同品牌 1/2/3 段、牛奶/羊奶）难区分、阈值 `1.05` 需规模校准、
    重排器 cross-encoder 在大量候选下的延迟/成本（需封顶重排候选数）。
