# 母婴垂类 ToB Agent

为**母婴行业企业**打造的垂类 AI Agent：**按企业产品结构定制知识库**，配套**知识转化工具链**
（爬虫 / OCR / 多源统一接口），以**端侧 1 家 1 实例**部署；企业**多名员工经个人微信共用同一 Agent**，
**每位员工会话相互独立**。

> ✅ 微信形态已确认为**个人微信（腾讯官方 iLink Bot API）**——标准开放协议、HTTP 长轮询，
> 非逆向方案，封号风险已消解（源码已读 `NousResearch/hermes-agent` 的 `gateway/platforms/weixin.py`）。

## 治理纪律（controlled-vibe-coding）

本项目采用「过程灵活、验收严格」的开发治理：

- **PRD 是唯一事实源**：见 [`prd/`](prd/)，按模块拆分、开发时一次只加载一个模块。
  - [`prd/00-charter.md`](prd/00-charter.md) — 意图 / 目标 / 非目标 / 范围边界 / **已确认决策 / 待确认**
  - [`prd/01-architecture.md`](prd/01-architecture.md) — 技术栈 / 目录 / 依赖图 / 关键决策
  - [`prd/02-index.md`](prd/02-index.md) — 模块索引（开发地图）
  - [`prd/modules/`](prd/modules/) — 6 个模块详解
- **验收必须真实运行**：见 [`harness/`](harness/)，每条可验证行为都有可执行脚本，
  `RESULT: PASS/FAIL`，绝不以「我觉得能行」替代。

## 模块一览

| 模块 | 职责 |
|------|------|
| MOD-knowledge-ingest | 爬虫 / PDF·图片表格 OCR / 多源统一适配接口，知识入库 |
| MOD-kb | 企业定制知识库：分块 / 嵌入 / 向量检索（企业间隔离） |
| MOD-agent | RAG 问答核心：检索增强 + 企业定制 prompt + **每企业可配置** LLM |
| MOD-session | 多员工会话隔离：`(企业×员工×会话)` 三级隔离（员工=from_user_id，借鉴 Hermes 会话键思路自研） |
| MOD-baby-profile | 宝宝/客户档案层：快速切换消歧 + 混合式建档安全网 + 主动归档 + 焦点宝宝档案注入 |
| MOD-wechat | **个人微信（自建 iLink Bot API 网关，方案 B）** 接入：消息收发 / 按 from_user_id 身份识别 / 去重 |
| MOD-deploy | 端侧 1 家 1 实例部署：Docker / 配置驱动 / 隔离（含 iLink bot 凭证） |

> 当前状态：PRD 治理骨架 + 6 模块**可实现规格**（含 harness 验收草案）已就位；**已进入实现阶段**，
> 首批 P 级任务（知识转化层、会话约束层）已交付并通过全量门禁（见下「实现进展」）。仍按 CVC 纪律：意图先行、每行为配 harness、改必全量回归。

## 实现进展

按 CVC 纪律，每个 P 级任务一次只做一块，且每行为必配真实运行 harness、改必全量回归。

| 提交 | 任务 | 关键交付 | 验收 |
|------|------|----------|------|
| `b5ca3fe` | 生产闭环真实适配器 + 独立重排器 | 6 个真实奶粉商品经 bge 真实嵌入入库；独立 cross-encoder 重排器 | 全量 7/7 绿 |
| `3f5f106` | **P1 知识转化层** | 统一多源适配接口（`KnowledgeRecord`/`IngestAdapter`/注册表）+ 真实零依赖爬虫 + 归一管线（路由/去重/容错） | harness I1–I6 绿 |
| `64fe970` | **P1 会话约束层**（规划·方向B + 记忆·方向A） | 用户约束收敛为 `UserConstraints`：规则抽取累积(B) + 超 N 轮 LLM 摘要压缩(A)，注入 system prompt 并持久化 | harness B1–B5/A1–A3 绿 |
| `a63d2aa` | **P2 宝宝/客户档案层** | `Customer(1→N BabyProfile)` + 每轮 LLM 实体链接(`resolve_and_extract`) + 混合式建档安全网/主动归档(`resolve_and_archive`) + 焦点宝宝注入 system | harness P1/P7/P8/P4/P5/P2/P3/P6/P9 全绿 |
| `d9bbb23` | **P2 数据一致性加固** | pending 防污染(`find_baby_by_name`仅 confirmed + `_match_known`同名歧义不误配) / 消歧失败熔断(`parse_failed`+会话级≥3轮降级告警) / 跨会话写锁(`_baby_locks`) / `prune_stale_pending` | harness P10–P15 全绿 |
| `a63d2aa` | **门禁提速治理** | 重型真实模型测试（`test_real_embed_bend`/`test_reranker` 的 RR3/RR4）改用 `RUN_REAL_MODEL=1` 显式开关隔离，默认门禁跳过 → 9/9 绿且 ~50s | 默认门禁 9/9 ALL GREEN，重型测试 opt-in 仍 7/7、4/4 绿 |

> **全量门禁：9/9 ALL GREEN**（`run_harness.py --all`，~50s）。重型真实模型测试默认跳过，
> 设 `RUN_REAL_MODEL=1` 并加 `--timeout 600` 可显式运行（bge 语义嵌入弯曲 7/7、真实重排 4/4 均绿）。

### 模块实现状态
| 模块 | 状态 | 说明 |
|------|------|------|
| MOD-knowledge-ingest | **partial（P1 已落地）** | 统一接口 + 真实爬虫 + 归一管线已交付；PDF/OCR 适配器 deferred（non-goal） |
| MOD-kb | partial | 分块/嵌入/向量检索 + 独立重排器已跑通真实嵌入 |
| MOD-agent | partial | RAG 核心 + 每企业可配置 LLM + 约束块注入已落地 |
| MOD-session | **partial（P1 已落地）** | 三级隔离 + 用户约束累积/压缩已交付 |
| MOD-baby-profile | **partial（P2 已落地 + 数据一致性加固）** | 客户 1→N 宝宝 + 每轮消歧 + 混合式建档安全网 + 主动归档 + 焦点注入；pending 防污染 / 消歧失败熔断 / 跨会话写锁 / 待确认清理 |
| MOD-wechat | partial | iLink Bot API 网关 + 约束/档案接线已落地 |
| MOD-deploy | backlog | 端侧 1 家 1 实例部署（待实现） |

## 运行验收

```bash
python3 scripts/run_harness.py --all        # 全量回归（CI 门禁，~50s，9/9 绿）
python3 scripts/run_harness.py --module kb  # 仅某模块
```

任一失败即退出非 0。新增行为必加测试；修 bug 必加回归。

> **重型真实模型测试默认跳过**：`test_real_embed_bend`（bge 语义嵌入 ~700MB+）与 `test_reranker` 的
> RR3/RR4（bge cross-encoder 重排）会加载重型模型、耗时且易触发 60s 默认超时变红。它们已用
> `RUN_REAL_MODEL=1` 显式开关隔离——日常门禁跳过，需要真实模型验证时：
> ```bash
> RUN_REAL_MODEL=1 python3 scripts/run_harness.py --timeout 600 --module real
> RUN_REAL_MODEL=1 python3 scripts/run_harness.py --timeout 600 --module reranker
> ```
> `test_reranker` 的 RR1/RR2（mock 透传 + 工厂契约，无模型）始终在默认门禁内运行。

## 已确认决策（见 charter C1–C5）

1. **微信 = 个人微信（腾讯官方 iLink Bot API）**：标准开放协议、HTTP 长轮询，非逆向，封号风险已消解。
2. **LLM = 每企业可配置**（端侧本地 / 云 API 按企业策略切换）。
3. **知识来源 = PDF/说明书 + 图片表格 + 爬虫**（均为非结构化，无 API）。
4. **首版 = 先定意图与方案，再进入编码**：意图与方案已细化完毕，现已进入 P 级实现（CVC 纪律：意图先行、每行为配 harness）。
5. **网关 / agent 核心 = 方案 B：仅借鉴 iLink 契约自建轻量网关**（不耦合 Hermes 运行时，Hermes `weixin.py` 仅作参考实现）。

## 决策状态（见 charter O1–O3′）

- ✅ **O1 向量库 = Chroma**（嵌入式 PersistentClient；2026-07-20 由 SQLite-vec 改换）。
- ✅ **O3′ 网关策略 = 方案 B 自建**（直接采用 Hermes 的方案 A 已否决）。
- ✅ **O2 首版写码切入点 = 端侧最小闭环优先**（知识库+问答+微信接入打通后，叠加 P 级增强）：已进入编码，P1 知识转化层与 P1 会话约束层已交付并通过全量门禁。
