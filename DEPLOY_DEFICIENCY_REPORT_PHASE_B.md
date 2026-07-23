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

### PB-3（观察·本轮未修）宝宝在 mock / 非 LLM Provider 下静默降级

项目默认 `llm.kind=mock`（离线/quick-start 即此模式）。宝宝建档每轮调用 LLM 消歧；mock provider 不返回可解析的 JSON，连续 3 轮 `parse_failed` 后网关把该会话**熔断降级为"仅产品问答"**：

```
WARNING wechat.gateway: 宝宝消歧连续失败 3 轮（>=阈值 3），本会话降级为仅产品问答
```

- **现象**：离线/默认模式下，宝宝档案主动建档、归档、消歧这条重要能力**整个失效**，且仅事后一条 WARNING，无主动状态提示。
- **风险**：① 默认开箱即丢失核心功能；② 降级是**按会话且不可逆**（仅成功解析才 `reset_resolution_fails`），一次 LLM 偶发返回异常 JSON 就可能永久降级该会话的宝宝能力。
- **建议**（未在本轮改动，避免误伤既有 resilient 设计）：a) 对瞬时解析失败用指数退避/重试而非立即累加熔断计数；b) 把"宝宝能力是否可用"作为会话级状态主动反馈给员工/后台，而非仅事后 WARNING。

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
| `harness/test_dataproc_feedback.py` | 新增回归 | PB-1 |
| `harness/test_phaseb_pending_guard.py` | 新增回归 | PB-2 |
| `harness/sim_full_pipeline.py` | 端到端模拟（隔离判定改用专有提问，修正误报） | 证据 |

## 5. 校验

```
pytest harness/test_phaseb_pending_guard.py harness/test_dataproc_feedback.py  → 4 passed
直接运行：test_store_pending_product / test_f3_kind_e2e / test_store_kind_routing /
         test_ingest_bundle_load / test_ingest / test_admin_dbstatus_scoped /
         test_store_hq_readonly  → 全部 PASS（无回归）
```

## 6. 建议（后续，未在本轮修复）

1. **PB-3**：mock/非 LLM 下宝宝能力静默降级——加重试 + 主动状态反馈（见 3.PB-3）。
2. **PB-1 延伸**：dataproc 对"已忽略文件"除 WARNING 外，可在 GUI/CLI 汇总里显式列出，并支持"移动至标准文件夹"的一键纠正，进一步降低真人误操作成本。
3. **PB-2 延伸**：当前是"推荐时提示"。更稳妥可改为"待确认商品默认不进入主动推荐候选集，仅在员工显式问到该品名时才带合规提示"，并把它与后台"确认"动作闭环（确认后自动解除限制）。
