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
| MOD-wechat | **个人微信（自建 iLink Bot API 网关，方案 B）** 接入：消息收发 / 按 from_user_id 身份识别 / 去重 |
| MOD-deploy | 端侧 1 家 1 实例部署：Docker / 配置驱动 / 隔离（含 iLink bot 凭证） |

> 当前状态：PRD 治理骨架 + 6 模块**可实现规格**（含 harness 验收草案）已就位；首版策略**先不写码、已细化完毕**，
> 待确认后进入实现（见 charter C4 / O2）。

## 运行验收

```bash
python3 scripts/run_harness.py --all        # 全量回归（CI 门禁）
python3 scripts/run_harness.py --module kb  # 仅某模块
```

任一失败即退出非 0。新增行为必加测试；修 bug 必加回归。

## 已确认决策（见 charter C1–C5）

1. **微信 = 个人微信（腾讯官方 iLink Bot API）**：标准开放协议、HTTP 长轮询，非逆向，封号风险已消解。
2. **LLM = 每企业可配置**（端侧本地 / 云 API 按企业策略切换）。
3. **知识来源 = PDF/说明书 + 图片表格 + 爬虫**（均为非结构化，无 API）。
4. **首版 = 先不写码**，先定意图与方案。
5. **网关 / agent 核心 = 方案 B：仅借鉴 iLink 契约自建轻量网关**（不耦合 Hermes 运行时，Hermes `weixin.py` 仅作参考实现）。

## 决策状态（见 charter O1–O3′）

- ✅ **O1 向量库 = Chroma**（嵌入式 PersistentClient；2026-07-20 由 SQLite-vec 改换）。
- ✅ **O3′ 网关策略 = 方案 B 自建**（直接采用 Hermes 的方案 A 已否决）。
- ⏳ **O2 首版写码切入点**：端到端最小闭环 / 知识库+问答优先 / 完整 6 模块？——**仍待你定**，定后才进入编码。
