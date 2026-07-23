# 真实部署场景不足分析（B端数据处理 → B端部署模型 → 员工使用）

> 方法：沿「1 企业 1 agent 端侧部署、多员工微信同 agent、单会话独立」的真实使用路径，
> 逐段拆解为真实操作步骤，对照源码定位不足。**两条高优不足已实跑探针坐实**（见 §附：探针结果）。
> 分析基准：`99d5139`（与 origin/main 同步）。
> 配套探针：`harness/probe_f3_kind_e2e.py`（F3 kind 丢失）、`harness/probe_dbstatus_leak.py`（db_status 泄露）。

---

## 0. 不足清单总览（按严重度）

| # | 阶段 | 不足 | 严重度 | 是否实锤 |
|---|---|---|---|---|
| D1 | ① 数据处理 | importer 丢弃 corpus 顶层 `kind`，Chroma 中 `kind` 恒为 `""` | 🔴 高 | ✅ 探针 P1 |
| D2 | ③ 员工使用 | agent 检索从不传 `kind_filter`/`kind_weight`，F3 意图路由双失效 | 🔴 高 | ✅ 静态+逻辑 |
| D3 | ② 部署 | `db_status` 全局 COUNT 无 `enterprise_id` 过滤，跨租户泄露规模 | 🟠 中 | ✅ 探针 P2 |
| D4 | ① 数据处理 | PP-Structure 表格识别已禁用 → 成分表/营养标签等表格结构丢失 | 🟠 中 | ✅ 静态 |
| D5 | ② 部署 | embedding 默认 `mock`；mock(262144维)↔bge(512维) 切换无保护必崩 | 🟠 中 | ✅ 静态 |
| D6 | ① 数据处理 | OCR 真实模型需联网下载(~300MB)且路径不可 GUI 注入，离线/内网端侧受阻 | 🟠 中 | ✅ 静态 |
| D7 | ② 部署 | admin 无 `AGENT_ADMIN_TOKEN` 时开发模式全放行 + HTML 页无认证 | 🟠 中 | ✅ 静态 |
| D8 | ③ 员工使用 | 待确认产品的确认/删除在微信侧未接线（仅 admin WebUI 可做） | 🟡 低 | ✅ 静态 |
| D9 | ① 数据处理 | bundle 中 article/ingredient 与 product_text 入库路径无差异（均 product_id=None） | 🟡 低 | ✅ 静态 |
| D10 | ② 部署 | Tauri 桌面端需 `webkit2gtk-4.1`+Rust 构建机，纯内网无构建环境出不了安装包 | 🟡 低 | ✅ 静态 |
| D11 | ① 数据处理 | GUI 仅支持 .md/.txt 预览，真实 PDF/图片无法预览，运营校验困难 | 🟡 低 | ✅ 静态 |
| D12 | ② 部署 | LLM `api_key` 明文写 yaml（P3）；HQ 只读未覆盖 product 表（设计，风险低） | 🟡 低 | ✅ 静态 |

> 亮点（非不足，避免误伤）：**多员工单会话隔离设计扎实**（`session/store.py` 主键 `(ent,emp,conv)` + 每会话 `asyncio.Lock` + `message_id` 去重；`wechat/gateway.py` 员工=`from_user_id`、DM 每员工一会话、宝宝消歧熔断）——这是该项目最稳的一块。

---

## ① B端数据处理（运营把产品册/文章/成分表转成可检索知识）

**真实使用拆解**：① 资料采集（PDF/扫描图片/Word）→ ② OCR 识别 → ③ 结构化+分类 → ④ 表格处理（成分表）→ ⑤ 产出 bundle → ⑥ 校验+入库 → ⑦ 待确认闭环。

### D1 🔴 importer 丢弃 corpus `kind`（实锤）
- **真实步骤⑥**：运营把 bundle 拷入收件箱，agent 启动 `scan_and_load` 自动入库。
- **当前行为**：`CorpusRecord.to_dict()`（`tools/dataproc/schema.py:43`）把 `kind` 写成 corpus.ndjson **顶层字段**；但 `importer._load_corpus`（`src/ingest/importer.py:68-79`）**只读 `part`+`meta`，完全不读顶层 `kind`**；`_add_corpus`（`src/kb/store.py`）仅取 `meta.get("kind","")`。→ Chroma 元数据 `kind` 恒为 `""`。
- **影响**：下游 F3 的"按内容类型路由/加权"在真实链路彻底失效；三类知识（product_text/article/ingredient）在检索时无法区分。
- **修复**：importer 注入 `meta["kind"] = rec.get("kind","")`（或给 `add_knowledge` 加 `kind` 参数）；并补一条"经 importer 入库后 Chroma 含正确 kind"的端到端回归（探针即可改造成测试）。

### D4 🟠 PP-Structure 表格识别禁用（成分表结构丢失）
- **真实步骤④**：母婴资料大量是**成分表/营养标签/喂哺量表**——典型表格。
- **当前行为**：`_ppstructure.py` 已禁用（`get_ppstructure()->None`，表格模型加载 >2min 被弃）；`image_table.py` 仅走 OCR + GapTree 排版（`tables=[]` 恒空）。
- **影响**：表格被拉平成文本流，行列关系、单元格对齐丢失；员工问"某成分含量多少"时，RAG 召回的是打散的文本，精度下降。
- **修复**：保留 PP-Structure 接口但提供可切换的轻量表格方案（如表格检测+单元格 OCR+结构化还原），或至少在部署侧给出"表格类资料请转 Excel/结构化录入"的明确引导。

### D6 🟠 真实 OCR 受离线/内网制约
- **真实步骤②**：端侧部署常在**内网/离线**环境。
- **当前行为**：`RUN_REAL_OCR=1` 触发 PaddleOCR，模型**首次联网下载 ~300MB**（`README` 称 Windows 自动改 `run_mode=paddle`）；**模型路径无 GUI 注入点**（依赖 PaddleOCR 默认/本地缓存）。
- **影响**：无外网的门店机器首次 OCR 直接失败或卡死；无法指定内网镜像/本地模型路径。
- **修复**：支持 `DATAPROC_PADDLE_MODEL_DIR` 等环境变量指向本地预置模型；部署文档明确"先在有网机器预下载模型再拷到端侧"。

### D9 🟡 article/ingredient 与 product_text 入库路径无差异
- **当前行为**：三者都走 `add_knowledge(..., product_id=None)`（`importer.py:79`），仅 `part`(b_kb/hq_kb) 区分库别，不区分 kind。
- **影响**：即便 D1 修好、kind 进入 Chroma，article 与 ingredient 在存储/检索上与 product_text 也**无结构差异**——F3 的"分库/差异化加权"价值被摊薄。
- **修复**：明确 kind 的语义边界（路由权重 vs 分表 vs 召回策略），避免"有 kind 但无差异"。

### D11 🟡 GUI 仅预览 .md/.txt
- **当前行为**：`GET /file_content` 仅支持 `.md`/`.txt`（`tools/dataproc/gui/backend/main.py`）。
- **影响**：真实资料是 PDF/图片，运营无法在 GUI 内预览核对 OCR 结果，只能盲导。
- **修复**：接入 PDF/图片渲染预览（前端用 pdf.js / `<img>`），或至少展示提取文本对照。

---

## ② B端部署模型（1 企业 1 agent 端侧装配并运行）

**真实使用拆解**：① 实例装配（固定 enterprise_id）→ ② 模型配置（embedding/LLM/rerank）→ ③ DB/向量库（Chroma 持久化、维度、并发）→ ④ 启动加载（收件箱）→ ⑤ 后台管理（admin WebUI）→ ⑥ 网关绑定（微信 token）→ ⑦ 安全（鉴权/隔离/脱敏）。

### D3 🟠 db_status 跨租户计数泄露（实锤）
- **真实步骤⑤**：企业管理员看"数据库状态"面板。
- **当前行为**：`db_status`（`src/admin/server.py:252-257`）执行
  `SELECT COUNT(*) FROM corpus / products_milk / products_nutrition`，**无 `WHERE enterprise_id`，函数签名也不收 enterprise_id**。
- **影响**：探针 P2 证明——以 `ent_X` 身份调用返回 `corpus_count=7`，而 ent_X 真实仅 2 条，泄露了 ent_Y(3) 与 hq(2) 的规模。在"共享 DB / 服务商托管多企业"场景下构成跨租户信息泄露；即便 1 企业 1 agent，也会把 HQ 共享库规模算进本企业，误导运营。
- **修复**：按 `cfg.enterprise_id` 收敛（`corpus WHERE enterprise_id=? OR enterprise_id='hq'` 视是否需要展示 HQ 而定），products 表同理。

### D5 🟠 embedding 默认 mock + 维度切换无保护
- **真实步骤②**：真实部署必须用语义嵌入。
- **当前行为**：`EmbeddingConfig.kind` 默认 `"mock"`（`src/common/config.py:30`）；mock=262144 维、bge=512 维（`src/common/embeddings.py:21-26`）。`KnowledgeStore` 按 `cfg.embedding.kind` 建 Chroma 集合（`src/app.py:20`）。
- **影响**：(a) 漏设 `AGENT_EMBEDDING_KIND=bge-small-zh` 时，检索退化为词袋（无语义，跨句同义召回差）；(b) **先用 mock 灌库再切 bge，Chroma 集合维度不匹配直接报错**，且无切换/重建保护。
- **修复**：部署校验（启动时若 DB 已存在且 embedding kind 与集合维度不符，明确报错或提供 `--reindex`）；默认给出"生产必须设 bge"的强提示。

### D7 🟠 admin 鉴权开发模式全放行
- **真实步骤⑦**：管理后台含 LLM 配置、员工/门店、微信 token。
- **当前行为**：`_verify_token`（`src/admin/server.py:67-74`）**无 `AGENT_ADMIN_TOKEN` 时直接放行**；且所有 HTML 页面路由（`/`、`/admin/*`）**无认证**。
- **影响**：生产若漏设 token（极易发生），管理后台与 API 全暴露；内网横向移动即可读配置状态、触发扫库。
- **修复**：生产模式强制 token（环境变量缺失则拒绝启动或仅绑 127.0.0.1 且告警）；HTML 页至少做一层轻校验。

### D10 🟡 Tauri 桌面端构建门控
- **真实步骤①**：运营想用桌面 GUI 做资料处理。
- **当前行为**：`pnpm tauri build` 依赖系统 `libwebkit2gtk-4.1-dev` + Rust 工具链（`README`），当前为环境门控、未实跑构建。
- **影响**：纯内网/无构建机的门店无法产出 Windows/macOS 安装包；只能走 `uvicorn` Web 模式（需 Python 环境）。
- **修复**：提供预构建安装包分发，或给出"构建机一次性产出 + 端侧直接安装"的流程；明确 CLI 模式（`run_data_import.py`）作为无 GUI 兜底。

### D12 🟡 api_key 明文 / HQ 只读边界
- **当前行为**：`POST /api/llm` 把 `api_key` 明文写 yaml（`server.py:217`，已自告警）；HQ 只读仅作用于 corpus 的 `delete/update`，product 表 `confirm/delete` 不检查 HQ 只读（设计：products 非只读，风险低）。
- **影响**：端侧 yaml 落盘明文密钥；若本实例即 hq，可改写 HQ 商品（设计容许，需知会）。
- **修复**：api_key 优先走 `AGENT_LLM_API_KEY` 环境变量，yaml 仅存占位；明确 HQ product 是否应只读。

---

## ③ 员工使用（多员工微信同 agent，单会话独立）

**真实使用拆解**：① 多员工接入（微信 DM）→ ② 会话隔离 → ③ 检索问答（RAG+LLM+引用+免责）→ ④ 意图路由（按问题类型选知识）→ ⑤ 写操作边界（待确认产品确认/删除）→ ⑥ 并发与稳定性。

### D2 🔴 agent 检索从不启用 kind 路由（实锤，双失效）
- **真实步骤③/④**：员工问"这个成分有什么用"（应优先 ingredient）、"某产品卖点"（应优先 product_text）。
- **当前行为**：`pipeline.py:119` 唯一检索调用
  `hits = self.store.retrieve(enriched_query, self.cfg.enterprise_id, top_k=5)` —— **既不传 `kind_filter` 也不传 `kind_weight`**。`kind_filter` 全仓仅定义在 `store.py`、从未被业务调用（`grep` 确认）。
- **影响**：即便 D1 修好使 kind 进 Chroma，**agent 侧也根本不按 kind 路由/加权**——F3 在端到端是"双重失效"（上游丢 kind + 下游不调用）。RAG 对所有知识一视同仁召回 top5，母婴垂类的"成分 vs 卖点 vs 科普"区分完全丢失。
- **修复**：在 `pipeline` 增加意图识别（轻量分类器/LLM 抽类型）→ 映射 `kind_filter`/`kind_weight` 传入 `retrieve`；并以 D1 的端到端测试守护。

### D8 🟡 待确认产品微信侧未接线
- **真实步骤⑤**：OCR 没识别注册号的产品落入 pending，需人工确认/删除。
- **当前行为**：`list_pending_products`/`confirm_product`/`delete_product` 数据侧原语就绪，admin WebUI 可调（`server.py`）；但**微信侧无对应指令**（review_round 标注为跨模块待办）。
- **影响**：运营只能在后台 WebUI 处理 pending，无法在员工对话流里"边聊边确认"，闭环割裂。
- **修复**：在 `wechat/gateway` 增加管理指令（如"确认产品 xxx"/"待确认列表"），复用 store 原语 + 权限校验。

### ③ 其他（非不足，已扎实）
- **会话隔离**（②步骤）：`session/store.py` + `wechat/gateway.py` 实现完整，主键隔离 + 锁 + 去重 + 宝宝消歧熔断，多员工互不串扰。
- **写操作边界**：`gateway.handle_message` 只调 `agent.answer`（只读检索+LLM），**不暴露 confirm/delete**——员工侧安全；写操作收敛在 admin（由 token 保护）。
- **引用+免责**：`pipeline.py` 附 `[引用i]` 与母婴健康免责，防幻觉有基础。
- **待补**：`retrieve` 默认 `top_k=5` 且无 rerank（`rerank_kind` 默认 `none`）→ 召回精度依赖 embedding 质量；mock 下仅词袋，需 bge + 可选 reranker 才达生产精度。

---

## 附：探针实证结果

**探针 P1 — F3 kind 丢失**（`harness/probe_f3_kind_e2e.py`）：
```
cid   title         Chroma.kind   结论
1     星飞帆卖点                    ❌ 丢失
2     辅食添加时机                   ❌ 丢失
3     DHA作用                      ❌ 丢失
❌ 实证结论：3/3 条 corpus 在 Chroma 中 kind 为空，F3 路由在真实入库链路完全失效。
```

**探针 P2 — db_status 跨租户泄露**（`harness/probe_dbstatus_leak.py`）：
```
[返回] corpus_count=7 products_milk=0 products_nutrition=0
[真实分布] {'ent_X': 2, 'ent_Y': 3, 'hq': 2}  →  ent_X 应有 2 条
❌ 实证结论：db_status 返回 7 条，而 ent_X 真实仅有 2 条 → 泄露 ent_Y(3) 与 hq(2) 规模。
```

---

## 修复优先级建议

1. **🔴 立即修（让 F3 真正生效）**：D1（importer 注入 kind）+ D2（pipeline 传 kind_filter/weight）+ 端到端回归测试。这两项是"假绿"根因，修完 F3 才名副其实。
2. **🟠 部署前必做**：D3（db_status 收敛）、D5（embedding 默认+bge 维度切换保护）、D7（admin 生产鉴权）。否则端侧一上线就有泄露/崩溃/裸奔风险。
3. **🟠 数据处理质量**：D4（表格）、D6（离线 OCR 模型预置）。
4. **🟡 体验/收尾**：D8（微信侧 pending 闭环）、D9（kind 语义差异）、D10（安装包分发）、D11（GUI 预览）、D12（密钥/只读边界）。

> 注：D1/D2/D3 均已具备最小修复路径与测试守护方案，可立即动手。
