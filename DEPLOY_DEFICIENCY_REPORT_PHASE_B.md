# 部署不足排查报告 · 第二阶段（Phase B：按真实部署流程重测）

> 承接 [DEPLOY_DEFICIENCY_REPORT.md](./DEPLOY_DEFICIENCY_REPORT.md)（第一阶段 D1–D12）。
> 本阶段按用户指定的**真实部署使用路径**逐步拆解、用「真人操作」模拟，重新排查不足：
>
> ① 用数据处理工具处理 B 端产品/育儿知识数据 → ② 在 B 端部署并把处理好的数据导入 → ③ B 端员工绑定网关并使用。
>
> **本轮在重新排查的同时，修复了 2 个严重不足（PB-1 静默丢数据、PB-2 待确认商品无护栏）并补了回归测试。**

---

## 0. 模拟方法（可重复）

- 全流程跑在全新临时沙箱（`tempfile.mkdtemp`，每次独立 DB/Chroma），避免旧会话 `message_id` 去重残留污染结果。
- 入口脚本：`harness/sim_full_pipeline.py`（端到端，含阶段 1/2/3）。
- 关键可观测项：dataproc `manifest`、导入后 SQLite/Chroma 计数、网关逐员工逐轮回复、会话隔离、去重、降级日志。

---

## 1. 阶段 1 — 数据处理（dataproc）不足

### PB-1（已修复）处理工具对「错放文件 / OCR 延迟」零可见反馈 → 资料静默丢失

模拟真人把资料整理进仓库：一个产品放在**错名文件夹** `产品资料wrong/`，一张**原料图片** `原料资料/原料标签.png` 在无 OCR 环境下处理。

`build` 结果：

```
异常仓库 build：
  products=1  corpus=2
  corpus 明细:
    - kind='product_text' title='正常产品.md'        content_len=18  ocr_pending=None
    - kind='ingredient'   title='原料标签.png'        content_len=0   ocr_pending=True   # 空占位
  操作者 stderr 反馈: 否（完全无可见提示）
```

**问题根因**（`tools/dataproc/build.py`）：

- 主循环只遍历 `TOP_FOLDERS`（产品资料/知识类文章/原料资料），**标准文件夹之外的文件从不进入 bundle，也不报任何错**——真人把文件拖错位置会"凭空消失"且不知情。
- 图片在无 OCR 时变成 `content=""` 的 `ocr_pending=True` 空占位，**占了一个 corpus 名额却没有真实内容**，而且 `manifest.counts` 不报告延迟数量、无任何 WARNING。

**修复**（`tools/dataproc/build.py`）：

- 新增 `_unmatched_files()`，build 结束后对不在标准总文件夹下的文件逐个 `logger.warning(...已被忽略...)`，并写入 `manifest["skipped_files"]`。
- `_process_nontext` 在返回 OCR 空占位时 `logger.warning(...OCR 未启用...延迟处理...)`。
- `manifest.counts` 新增 `ocr_pending` 与 `skipped_files` 计数。

**回归测试**：`harness/test_dataproc_feedback.py`（断言 skipped_files≥1、ocr_pending≥1、且确有 WARNING 日志）。

---

## 2. 阶段 2 — B 端部署 + 数据导入

模拟把 bundle 放进收件箱、启动实例自动加载（`BUNDLE_INBOX_DIR` + `build_instance` → `load_on_startup`）。

```
corpus 条数(ent_sim)=10  产品条数(ent_sim)=2  待确认商品数=1
  pending: 臻羊婴儿配方羊奶粉 [products_milk] 缺失字段=reg_number
```

**本阶段确认项（非缺陷）**：

- **D1（kind 真实入库）仍然有效**：`b_kb` 语料携带 `kind`（`product_text/article/ingredient`），F3 路由侧 `kind_weight=1.8` 已接线（`src/agent/pipeline.py:168`）。
- **D3（`db_status` 跨租户泄露）已修复仍有效**：统计按 `enterprise_id` 过滤。
- **Chroma 物理隔离正确**：向量目录由 `db_path` 派生（`instance.chroma`），每个实例独立，无跨运行串库。
- **`corpus=10` 是预期值**，不是泄漏：每个奶粉产品被切成 3 个语义块（`b_milk` 基础信息/配料表/营养成分）+ 4 条 `b_kb` 知识 = 10。

> 备注：检索抽样里 `top_kind=None`（如 "DHA有什么作用" 返回的是含 DHA 字样的奶粉产品块而非 ingredient 文档）是 **mock 向量（哈希非语义）假象**，不是路由缺陷；真实 `bge` embedding 下 `kind_weight` 会正确加权。阶段 1 的 `b_kb` 记录 `kind` 已确定写入 Chroma（见上）。

---

## 3. 阶段 3 — 员工绑定网关并使用

模拟两名员工（`employee_zhang` / `employee_li`）各自在微信侧发问，注入 `noop` 发送避免触网。

### PB-2（已修复）待确认（未注册）商品被直接推荐给客户，无任何护栏 —— 合规风险

员工问"臻羊羊奶粉多少钱、适合什么宝宝？"，而臻羊的 `reg_number` 为空（待确认）。修复前 agent 原样推荐：

```
根据企业知识库，为您推荐：臻羊婴儿配方羊奶粉，（臻羊，羊奶粉，1段段），
适用0-6个月，官方参考价 398 元，特点：100%纯羊乳蛋白、低致敏。
```

即：**未注册的婴幼儿配方奶粉被当作正常商品推荐给客户**。门店后台虽有"待确认列表"（`store.list_pending_products`），但**微信推荐路径完全没有接入该状态**。

**修复**（`src/kb/store.py` + `src/ingest/importer.py` + `src/agent/pipeline.py`）：

- `add_milk` / `add_nutrition`：把"待确认"状态（`reg_number`/`health_license` 空）写入产品分块元数据 `pending`。
- importer：product_text 语料按 `product_uid→pid` 绑定后，把该商品 `pending` 状态写入 `meta`。`retrieve` 命中回查 SQLite `meta_json`，故 agent 端可见。
- `pipeline.answer`：命中含 `pending` 商品且该商品被点名推荐时，追加合规提示：

  > `【合规提示：臻羊婴儿配方羊奶粉 注册号待确认，暂不建议主动向客户推荐，请先在企业后台完成确认。】`

修复后同一条问询输出：

```
…特点：100%纯羊乳蛋白、低致敏。【合规提示：臻羊婴儿配方羊奶粉 注册号待确认，
暂不建议主动向客户推荐，请先在企业后台完成确认。】 （温馨提示：…）
```

**回归测试**：`harness/test_phaseb_pending_guard.py`（importer 打标 + agent 对 pending 商品提示 / 对已注册商品不提示）。

### PB-3（已修复）宝宝在 mock / 非结构化 LLM 下静默降级

项目默认 `llm.kind=mock`（离线/quick-start 即此模式）。宝宝建档每轮调用 LLM 消歧；**mock provider 不返回可解析的 JSON**，连续 3 轮 `parse_failed` 后网关把该会话**熔断降级为"仅产品问答"**：

```
WARNING wechat.gateway: 宝宝消歧连续失败 3 轮（>=阈值 3），本会话降级为仅产品问答
```

- **根因**：宝宝消歧 `resolve_and_extract` 每轮都调 LLM 并解析 JSON；mock provider 结构性**无法**返回结构化 JSON，于是"每轮解析失败→累加熔断→3 轮后降级"在默认模式下**每个会话必然发生**——这是能力结构性不可用，却被当成"连续失败"处理。
- **风险**：① 默认开箱即丢失核心功能（宝宝建档/归档/消歧整个失效），且仅事后一条 WARNING；② 降级按会话且不可逆（仅成功解析才 `reset_resolution_fails`），一次 LLM 偶发异常 JSON 就可能永久降级该会话宝宝能力。

**修复**（`src/agent/providers.py` + `src/session/store.py` + `src/wechat/gateway.py`）：

- `LLMProvider` 增加能力标记 `supports_structured`（默认 `True`）；`MockProvider` 显式 `supports_structured = False`（mock 不返回结构化 JSON）。
- 网关计算 `baby_capable = baby_profile_enabled and baby_store 存在 and provider.supports_structured`：
  - **能力可用**（ollama/cloud 等）：维持原消歧/建档 + 熔断逻辑不变；
  - **能力不可用**（如 mock）：**跳过 LLM 消歧/建档、不累加熔断计数、不静默降级**，仅首次记录会话级能力状态 `unavailable` 并**主动告警一次**，明确提示"配置支持结构化输出的 LLM 后可启用"。
- `session` 新增 `session_baby_capability` 表 + `set_baby_capability`/`get_baby_capability`，使"宝宝能力是否可用"成为可观测的会话状态（后续可对接员工/后台展示）。

**修复后实测**（mock 模式发 3 条产品问）：
```
网关 WARNING/降级类: （无）            # 不再有「降级」静默降级
宝宝档案能力不可用 … 本会话跳过宝宝建档/归档，仅做产品与育儿问答  # 每会话仅一次主动告警
```

**回归测试**：`harness/test_baby_capability_mock.py`（4 passed）：能力标记默认值、会话能力状态 round-trip、mock 模式不降级且记为 unavailable、结构化 provider 仍走熔断降级。

### 3.x 已确认非缺陷项（澄清，避免误报）

| 项 | 结论 | 证据 |
|---|---|---|
| 多员工会话隔离 | **正确** | session key=`ent_sim:employee_zhang:employee_zhang` ≠ `…employee_li…`；用"对方专有提问是否串入本方历史"判定，`串入=False` |
| `message_id` 去重 | **正确** | 重发同 `message_id` → `handle_message` 返回 `None`、不产生新发送 |
| 之前 Stage 3 的 `None` 崩溃根因 | **测试假象，非产品缺陷** | 旧运行在同一 DB 上残留 `emp_A-0` 等 `message_id`，重跑时去重返回 `None`；换全新 DB 后全程 `OK`，无 `None` |
| F3 路由接线 | **正确** | `kind` 真实落 Chroma（D1）；`kind_weight` 传入 `retrieve`（D2）；mock 下路由"不准"是哈希向量假象 |

---

## 4. 本轮改动清单

| 文件 | 改动 | 对应 |
|---|---|---|
| `tools/dataproc/build.py` | 错名文件夹/ OCR 延迟的 WARN + `manifest.skipped_files` / `counts.ocr_pending` | PB-1 |
| `src/ingest/importer.py` | product_text 语料按商品绑定写入 `pending` 标记 | PB-2 |
| `src/kb/store.py` | `add_milk`/`add_nutrition` 分块元数据带 `pending` | PB-2 |
| `src/agent/pipeline.py` | 推荐 pending 商品时追加合规提示 | PB-2 |
| `src/agent/providers.py` | `LLMProvider.supports_structured` 能力标记；`MockProvider=False` | PB-3 |
| `src/session/store.py` | 会话级宝宝能力状态 `session_baby_capability` + 读写方法 | PB-3 |
| `src/wechat/gateway.py` | 能力不可用时跳过宝宝消歧/建档、不降级、仅一次主动告警 | PB-3 |
| `harness/test_dataproc_feedback.py` | 新增回归 | PB-1 |
| `harness/test_phaseb_pending_guard.py` | 新增回归 | PB-2 |
| `harness/test_baby_capability_mock.py` | 新增回归 | PB-3 |
| `harness/sim_full_pipeline.py` | 端到端模拟（隔离判定改用专有提问，修正误报） | 证据 |

## 5. 校验

```
pytest harness/test_phaseb_pending_guard.py harness/test_dataproc_feedback.py \
      harness/test_baby_capability_mock.py  → 12 passed
直接运行：test_store_pending_product / test_f3_kind_e2e / test_store_kind_routing /
         test_ingest_bundle_load / test_ingest / test_admin_dbstatus_scoped /
         test_store_hq_readonly / test_baby_profile / test_ultimate_baby_harness /
         test_cross_context_pollution / test_session_constraints / test_wiring /
         test_query_enrichment / test_providers …  → 全部 PASS（无回归）
```

## 6. 建议（后续）

1. **PB-1 延伸**：dataproc 对"已忽略文件"除 WARNING 外，可在 GUI/CLI 汇总里显式列出，并支持"移动至标准文件夹"的一键纠正，进一步降低真人误操作成本。
2. **PB-2 延伸**：当前是"推荐时提示"。更稳妥可改为"待确认商品默认不进入主动推荐候选集，仅在员工显式问到该品名时才带合规提示"，并把它与后台"确认"动作闭环（确认后自动解除限制）。
3. **PB-3 延伸（可选）**：真实 LLM 偶发返回异常 JSON 仍会触发熔断降级（现有 resilient 设计，成功解析即复位）；如需更稳，可对瞬时解析失败做指数退避重试，再计入熔断计数。
4. **PB-1 / D4（图片原生 + 版面复杂资料解析）**：已确定 **v6-only 方案**——PP-OCRv6 medium，完整安装 `paddleocr[all]>=3.7.0` + PaddlePaddle 3.x（已实现并实测验证，见 [`DATAPROC_IMAGE_LAYOUT_TIER.md`](./DATAPROC_IMAGE_LAYOUT_TIER.md)）。PB-1 经 Tier A + `ocr_pending` 可见 WARN 闭合；**PaddleOCR-VL（Tier B）暂不接入**，故 **D4（成分表等表格结构）当前仍为已知限制**——v6 只抽扁平文本，表格结构丢失，需运营在 GUI 转 Excel/结构化录入兜底。D6（离线预置模型）、D11（GUI 预览提取文本）已由 v6 完整安装覆盖。
   - **2026-07-23 补充**：适配器已按官方 PP-OCRv6 Quick Start 重写（默认 PP-OCRv6_medium + 三项全关 + 官方 paddle 动态图引擎），并完成 OCR 板块"算法优化"审计——与官方精度方案真正冲突的"1600px 预缩放"此前已删；monkey-patch `set_optimization_level=0` 经核实为 **PaddlePaddle 3.3.1 onednn 的 PIR bug 兼容性补丁（精度零损失）**，CPU 路径必现、不打补丁官方引擎直接 `NotImplementedError`，故保留；其余长图切片 / tbpu 低置信阈值均为精度中立/改善项，保留。5 张母婴产品图实测基准：平均 **56.6s/图**（CPU medium，精度优先）、识别 1584 字、反光液体袋图自动 `low_conf=True` 进 Tier C 闭环；基准脚本见 `tools/dataproc/bench_ocr_5images.py`。
