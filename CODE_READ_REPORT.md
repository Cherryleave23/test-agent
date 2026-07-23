# 源码整读报告 · 合并至 `99d5139`（27 个新提交）

> 触发：用户要求「从 GitHub 获取最新版本并派出多个子代理完整阅读最新源码」。
> 方法：本地 `git merge --ff-only origin/main` 已快进到 `99d5139`；按模块并行派出 5 个子代理通读，
> 再由主代理交叉验证关键结论。本报告为整合产物。
> 范围：179 文件、+19079/−529（已排除 `cde/` 示例数据与二进制）。

---

## 0. 总体健康度结论

**核心数据层（F1–F6）与 P0/P1/P2 安全修复全部保留，无破坏性回归 → 核心层 GREEN。**
但有 **1 个高优先级集成缺陷**（F3 的 `kind` 在真实入库路径未生效）与 **1 个低危计数泄露**需要后续闭环。
评分延续 review_round6 的 **5.0（0 P0/P1/P2）**，新增问题均为 P2 级或更低。

---

## 1. 核心数据层（`src/kb/store.py` · `baby/store.py` · `ingest/importer.py` · `app.py`）

| 关注点 | 结论 |
|---|---|
| 三库模型 | SQLite（corpus/products/hq_products/ingest_dedup）+ Chroma 向量库，RRF 混合检索 |
| **F2 只读** | ✅ `_row_readonly` 以 `enterprise_id == HQ_ENT` 为权威；`add_hq_knowledge` 不再污染 content-type 的 meta（保持空 dict，F1 纯度保留） |
| **F3 kind 路由/加权** | ⚠️ store 侧逻辑完好（`kind` 入 Chroma metadata；`retrieve` 支持 `kind_filter` 路由 + `kind_weight` 乘分，默认 `None` 兼容）——但**经 importer 的真实入库路径 kind 丢失**（见 §6 高优项） |
| **F4 bundle** | ✅ 加载顺序 products→hq_products→corpus；`uid_to_pid` 绑定；`scan_and_load` 成功→`processed/`、失败→`failed/`（move 幂等）；`load_on_startup` 读 `BUNDLE_INBOX_DIR` |
| **F5 待确认** | ✅ `_PENDING_COL={"products_milk":"reg_number","products_nutrition":"health_license"}`；`confirm/delete` 带 `enterprise_id` 跨租户校验（P0-04） |
| 安全修复 | ✅ 全部 `db_tx`（finally 关闭）无连接泄漏；`update_corpus` 在连接关闭后重索引（修 P2-25）；`retrieve` `$or[ent,HQ_ENT]` 回查（F6 保留） |
| `baby/store.py` 新增 | `customers`/`babies` 表新增 `birth_date`/`gestational_weeks`/`medical_history_json`/`feeding_history_json`；P0-3 健康字段加密（`_enc/_dec`）；per-baby 写锁（线程安全） |

**公共方法签名**（节选，F2–F5 相关）：
```
add_hq_knowledge(title, content, meta=None) -> int
add_hq_product(fields, meta=None) -> int
get_hq_products() -> List[dict]
list_pending_products(enterprise_id) -> List[dict]
confirm_product(product_id, value, table="products_milk", enterprise_id=None)
delete_product(product_id, table="products_milk", enterprise_id=None)
add_knowledge(enterprise_id, title, content, meta=None, product_id=None) -> int
delete_corpus(cid, enterprise_id=None) / update_corpus(cid, ..., enterprise_id=None)
retrieve(query, enterprise_id, top_k=5, filters=None, kind_filter=None, kind_weight=None)
_row_readonly(row) [staticmethod]            # ent==HQ_ENT 优先，兜底 meta.readonly
class ReadonlyError(Exception)
```

**轻微漂移（非破坏）**：`delete_product` 用硬编码 `("products_milk","products_nutrition")` 而非 `_PENDING_COL`，与 `confirm_product` 不一致，表集合相同无功能影响。

---

## 2. 管理后台 WebUI（`src/admin/*`，新增）

- **框架**：FastAPI；路由在 `create_app(cfg)` 内注册；鉴权 `_verify_token`（HTTPBearer + `AGENT_ADMIN_TOKEN`），无 token 时开发模式放行；**HTML 页面路由无认证**。
- **端点（按组）**：
  - LLM：`GET/POST /api/llm`、`GET /api/llm/models`
  - 数据库：`GET /api/database/status`、`POST /api/database/scan`、`GET /api/database/pending`、`POST /api/database/confirm`、`DELETE /api/database/product`
  - 门店/员工：`GET/POST /api/stores`、`GET/POST /api/employees`、`DELETE /api/employees/{id}`、`POST /api/employees/batch-delete`
  - 微信网关：`GET/POST /api/gateway`、`DELETE /api/gateway/{id}`、`start|stop|status|qrcode` 等
  - 宝宝档案（只读）：`GET /api/babies`、`GET /api/babies/{id}`
- **安全审计**：跨租户隔离 ✅（所有查询 `enterprise_id` 取自 `cfg.enterprise_id`，拒绝客户端传入；`confirm/delete` 越权返 403、删员工/解绑/宝宝详情越权返 404/403）；XSS ✅（`pages.py` 全程 `html.escape` + `mask_token` 脱敏）；连接泄漏 ✅（`db_tx`）；线程安全 ✅（`_get_store` 锁 + 双重检查）。
- **与核心层交互**：均经 `cfg.enterprise_id` 调用，未发现跨企业越权路径。注意：**HQ 只读仅作用于 corpus 的 `delete/update`，product 表的 confirm/delete 不检查 HQ 只读**（设计上 product 非只读，风险低）。
- **models.py**：新建 `admin_stores`/`admin_employees`；`ALLOWED_TABLES={"products_milk","products_nutrition"}` 白名单；复用 kb/baby 现有表为只读。

---

## 3. OCR / 结构化引擎（`tools/dataproc/adapters` · `build.py` · `classifier.py`）

- **Adapter 契约**：`build.build_bundle(repo_dir, out_dir, selection, progress_cb)`；`get_adapter(ext).extract(path, run_real_ocr)` 返回 `AdapterResult{text, meta, tables, low_conf}`。
- **PaddleOCR 3.x 官方 API**：`PaddleOCR(lang="ch", engine="paddle_static", engine_config={device_type:cpu, cpu_threads:4, run_mode:mkldnn}, text_detection_model_name="PP-OCRv6_small_det", text_recognition_model_name="PP-OCRv6_small_rec", use_doc_orientation_classify=False, use_doc_unwarping=False)`；单例 + 双重检查锁；含 Windows PIR bug 的 monkey-patch。
- **PP-Structure 已禁用**：`get_ppstructure()->None`、表格识别加载 v5 server 模型 >2min 被弃；`image_table.py` 仅走 OCR + GapTree 排版，`tables=[]` 恒空（留接口兼容）。
- **tbpu 排版解析**：`process_ocr_lines(lines, parser_key)` → 8 个解析器（multi/single × para/line/none + single_code）；`gap_tree.py` 用竖切线切分多栏并按人类阅读顺序排序；`paragraph_parse.py` 按左右边距 + 行距阈值（TH=1.2）切自然段；`line_preprocessing.py` 估算旋转角生成 `normalized_bbox`。
- **性能**：mkldnn + mobile + PP-OCRv6_small ≈ 10s/图。**注意**："56s→5s" 的优化在**当前代码未体现**——预缩放被刻意去掉（精度暴跌），实际为 v6_small+mkldnn 的 10s 级。
- **classifier.py**：`classify(text)` 返回 `{ptype, product_category}`，决定 product 的 milk/nutrition **不**决定 corpus kind（corpus kind 由文件夹定）。
- **LLM tier**：已接入，`build` 用 `from_config(cfg.llm)` 的 provider 对 product_text 做 `structure(content, provider)`；`none/openai/ollama` 由 `DATAPROC_LLM_*` 控制；缺 provider 走纯规则兜底。
- **Bundle 契约**：字段名/结构与 importer **完全匹配**，`enterprise_id`、`uid→pid` 一致。⚠️ 详见 §6 高优项（corpus `kind` 写而未消费）。

---

## 4. 数据导入 GUI（`tools/dataproc/gui`，FastAPI 后端 + React 前端）

- **后端端点**：`/repos`、`/tree`(+`/full`/`mkdir`/`rmdir`/`file`/`move`)、`/file_content`、`/upload`、`/process`(+`/status`)、`/processed`、`/bundle`、`/settings`、SPA 兜底。
- **每仓库独立输出目录**：`repos.py` 写入 `<repo>/.dataproc/repo.json` 的 `output_dir`；优先级 `实参 → repo.output_dir → settings.output_dir → <repo>/.dataproc/bundle`。
- **Obsidian 风格文件树**：`tree.py` 递归整树 + 越界校验；前端 `TreePanel` 支持展开/折叠、多选、`processed-dot`、右键菜单、HTML5 拖拽移动、单击预览。
- **进度**：**轮询**（非 WS/SSE），前端 `setInterval(poll,1500)`。
- **OCR 引擎注入**：SettingsPanel 暴露 `ocr_enabled`/`run_real_ocr` → 处理前写环境变量 `DATAPROC_OCR_ENABLED`/`RUN_REAL_OCR`；**模型路径无 GUI 注入点**（依赖 PaddleOCR 默认）。
- **API 契约**：`VITE_API_BASE` 构建期注入、默认同源回退；Tauri 系统拖入走 `@tauri-apps/plugin-fs`。
- **Tauri 构建门控**：依赖 `libwebkit2gtk-4.1-dev` + Rust 工具链；`pnpm-workspace.yaml`、`src-tauri/icons/icon.png` 已就绪；命令 `pnpm install → pnpm --filter dataproc-gui build → pnpm tauri build`。**属环境门控，非代码缺陷**。

---

## 5. 测试覆盖与评审文档（`harness/*` · `review_round2~6.md` · `README.md` · `MOD-admin.md`）

- **F1–F6 store 侧守护**：`test_store_hq_readonly`(F2/F6)、`test_store_kind_routing`(F3)、`test_ingest_bundle_load`(F4)、`test_store_pending_product`(F5) 四个专用回归文件全绿。
- **覆盖盲区**：
  - 🔴 F3 端到端：测试直接调 `add_knowledge(meta={"kind":...})` 绕过 importer，未覆盖真实入库路径（即 §6 高优项）。
  - 🟠 API 层：HQ 只读拒绝 / pending 列表未断言；`GET /api/database/pending` 仅在 A13/A14 间接触及。
  - 🟠 真实 embedding 下的 bundle 加载与 uid→pid（门禁仅 mock Chroma）。
  - 🟠 微信侧 UX 接线（F5 确认/删除经微信）、agent 意图识别侧（F3 选 kind）均为跨模块待办。
- **安全演进**：R2 P0 100% 修复 → R3 P1 全部修复 → R4 P1-N1~5 全部修复 → R5 P2 5/6 + R6 全部修复 → **R6 起 0 P0/P1/P2（5.0）**。
- **遗留 P3 项**（低危，可后续清理）：kb 11 处冗余 commit、HTML 页面无认证、customer TOCTOU、`get_vault` 未 `require=True`、test `connect` 泄漏、`api_key` 明文、Chroma 孤儿向量、`esc()` JS 上下文 XSS、`mask_token` 短 token 不脱敏等。

---

## 6. ⚠️ 需闭环的发现（按优先级）

### 🔴 高优 · F3 `kind` 在真实入库路径丢失（集成缺陷）
- **现象**：`CorpusRecord.to_dict()` 把 `kind` 写成 corpus.ndjson **顶层字段**（schema.py:43），而 `meta` 独立（build.py:270 corpus meta 仅 `{source,path}`）。importer corpus 分支只读 `part`+`meta`，**未读顶层 `kind`**；F3 的 `_add_corpus` 仅取 `meta.get("kind","")`，故 Chroma 中 `kind` 恒为 `""`。
- **后果**：经 dataproc 入库的 corpus 无法被 `kind_filter`/`kind_weight` 正确路由/加权 —— F3 在真实链路实际不生效（单测绿灯是因为绕过了 importer）。
- **建议修复**（最小侵入）：importer 在 `add_knowledge`/`add_hq_knowledge` 前注入 `meta["kind"] = rec.get("kind", "")`，使 `_add_corpus` 自然拾取；同时补一条「经 importer 入库后 Chroma 含正确 kind」的端到端回归测试。

### 🟠 中低 · `db_status` 全局 COUNT 无 `enterprise_id` 过滤（server.py:255-257）
- **现象**：`SELECT COUNT(*) FROM corpus/products_milk/products_nutrition` 跨全库计数。
- **后果**：共享 DB 部署下泄露其他租户规模；纯 1 企业 1 agent 部署下仅多计 HQ 共享库，影响低。
- **建议**：按 `cfg.enterprise_id` 收敛（corpus 需 `WHERE enterprise_id=? OR enterprise_id='hq'`）。

### 🟡 信息项 · PP-Structure 已禁用、性能数字需校准
- PP-Structure 表格识别因模型加载 >2min 被弃，当前仅 OCR+GapTree 排版；README/评审中 "56s→5s" 应校准为 "v6_small+mkldnn ≈10s/图，预缩放被刻意去掉"。

---

## 7. 建议的下一步
1. **修复 §6 高优项**：让 F3 `kind` 经 importer 真实生效 + 补端到端测试（建议作为 F3 收尾）。
2. 收敛 `db_status` 计数作用域（P2 清理）。
3. 推进跨模块接线（F3 微信侧意图识别、F5 微信侧确认/删除 UX）——数据侧原语已就绪。
4. 校准 README/评审中性能表述，避免误导。

---
*生成于本地 `99d5139`，子代理并行通读 + 主代理交叉验证（db_status 全局计数、importer corpus kind 来源、dataproc corpus 写出位置均已实读确认）。*
