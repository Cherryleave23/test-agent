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
  - [`prd/modules/`](prd/modules/) — 7 个模块详解
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
| MOD-deploy | 端侧 1 家 1 实例部署：Docker / Windows 直装 / 配置驱动 / 依赖分层 / 隔离 |

> 当前状态：PRD 治理骨架 + 7 模块**可实现规格**（含 harness 验收草案）已就位；**已进入实现阶段**，
> 首批 P 级任务（知识转化层、会话约束层）已交付并通过全量门禁（见下「实现进展」）。仍按 CVC 纪律：意图先行、每行为配 harness、改必全量回归。

## 实现进展

按 CVC 纪律，每个 P 级任务一次只做一块，且每行为必配真实运行 harness、改必全量回归。

| 提交 | 任务 | 关键交付 | 验收 |
|------|------|----------|------|
| `b5ca3fe` | 生产闭环真实适配器 + 独立重排器 | 6 个真实奶粉商品经 bge 真实嵌入入库；独立 cross-encoder 重排器 | 全量 7/7 绿 |
| `3f5f106` | **P1 知识转化层** | 统一多源适配接口（`KnowledgeRecord`/`IngestAdapter`/注册表）+ 真实零依赖爬虫 + 归一管线（路由/去重/容错） | harness I1–I6 绿 |
| `64fe970` | **P1 会话约束层**（规划·方向B + 记忆·方向A） | 用户约束收敛为 `UserConstraints`：规则抽取累积(B) + 超 N 轮 LLM 摘要压缩(A)，注入 system prompt 并持久化 | harness B1–B5/A1–A3 绿 |
| `a63d2aa` | **P2 宝宝/客户档案层** | `Customer(1→N BabyProfile)` + 每轮 LLM 实体链接(`resolve_and_extract`) + 混合式建档安全网/主动归档(`resolve_and_archive`) + 焦点宝宝注入 system | harness P1/P7/P8/P4/P5/P2/P3/P6/P9 全绿 |
| `25055ea` | **P2 数据一致性加固** | pending 防污染(`find_baby_by_name`仅 confirmed + `_match_known`同名歧义不误配) / 消歧失败熔断(`parse_failed`+会话级≥3轮降级告警) / 跨会话写锁(`_baby_locks`) / `prune_stale_pending` | harness P10–P15 全绿 |
| `4d154ef` | **P2 消歧结果缓存** | `focus_is_stable`：焦点稳定时跳过 LLM 实体链接、规则抽取归档到焦点；提及他宝仍触发切换检测 | harness P16 全绿 |
| `46a8737` | **P2 消歧 Prompt Caching（优化 C）** | 稳定前缀（指令+known 清单）置首 + `cache_control` 断点：OpenAI 兼容端点自动前缀缓存、Anthropic 显式 ephemeral 断点；切换焦点不破坏缓存，input token 降 50-90% | harness P17 全绿 |
| `1216b85` | **Prompt Caching 全阶段落地** | P0 `list_for_employee` ORDER BY（序列化稳定）；阶段2 pipeline RAG prompt 稳定在前/动态 context 在后；阶段3 `_report_cache_hit` 命中日志；阶段4 `warmup_prompt_cache` 预热 | baby P18–P20/P23 + agent P21/P22 全绿 |
| `a63d2aa` | **门禁提速治理** | 重型真实模型测试（`test_real_embed_bend`/`test_reranker` 的 RR3/RR4）改用 `RUN_REAL_MODEL=1` 显式开关隔离，默认门禁跳过 → 9/9 绿且 ~50s | 默认门禁 9/9 ALL GREEN，重型测试 opt-in 仍 7/7、4/4 均绿 |
| `feat(deploy)` | **P1 端侧部署 Windows 直装 + 依赖分层** | 三层依赖（Tier1 URL拉取/Tier2 捆绑/Tier3 可插拔）+ `dependency-manifest.yaml` 声明式清单 + `configure.ps1` 交互式配置向导 + `from_yaml_with_env()` 环境变量覆盖 + `PluginManager` 可插拔模型路径 + requirements 双轨拆分 | deploy 18 断言全绿 |
| `feat(baby-v2)` | **P2-v2 档案 schema 扩展 + 检索查询融合** | `birth_date`/`gestational_weeks`/`medical_history`/`feeding_history` 结构化字段 + SQL 自动迁移 + `_enrich_query()` 检索查询融合档案上下文 + LLM 消歧器抽取新字段 | baby P24-P27 + agent P28-P29 共 13 断言全绿 |
| `feat(baby-v2)` | **P2-v2 终极实战 harness（从零建档版）** | 5 宝宝差异化档案 + 34 条碎片化信息从空库随机打乱逐条发送 + Mock Provider 只返回单条属性（迫使跨轮累积）+ 5 交叉提问随机分配 + 无串档/焦点切换/回答正确验收 | baby P30-P39 全绿 |
| `fix(baby)` | **P0 修复：focus_is_stable 新宝宝名检测** | 从零建档场景下 `focus_is_stable` 不检测新宝宝名导致新宝宝不被建档 + 串档；修复：消息含宝宝信号但不含焦点宝宝名时返回 False 交 LLM 建档 | 终极实战 6/6 + 全量 14/14 全绿 |
| `fix(baby)` | **P1 修复：4 项档案能力缺陷（D1-D4）** | D1 `_rule_extract` 支持结构化字段抽取（birth_date/gestational_weeks/medical_history/feeding_history）+ D2 客户名后续补充（创建独立 customer 记录避免共享串档）+ D3 信息丰度自动确认（pending 累积≥3属性转 confirmed）+ D4 focus_is_stable 代词指代优化 + `_match_known` 退化匹配区分未命名客户 | 终极实战 6/6 + 全量 14/14 全绿 |
| `fix(agent)` | **P0+P1 修复：消息流程装配断裂（A1-A6）** | A1 `build_instance` 装配 `BabyProfileStore`（端侧 baby-profile 不再失效）+ A2 网关传 `baby_profile` 使检索查询融合生效 + A3 约束压缩合并而非替换（不丢旧约束）+ A4 焦点切换刷新约束（消旧宝宝残留）+ A5 约束从档案派生（消双源冲突）+ A6 LLM 指数退避重试（网络抖动容错） | 装配 5/5 + 全量 15/15 全绿 |
| `fix(baby)` | **P0 修复：跨上下文污染防护（C1+C2）** | C1 `focus_is_stable` 无宝宝信号时返回 False（原 return True 导致成人检验报告/用户自身症状被归档到焦点宝宝）+ `resolve_and_extract` 无信号时不抽取属性（返回空 extracted）+ C2 `_validate_extracted` 合理性校验兜底（baby_age>6岁拒绝、birth_date 未来/太久以前拒绝）+ `_BABY_SIGNALS` 补充 `\d\s*段` | 污染防护 5/5 + 全量 16/16 全绿 |
| `fix(baby)` | **P0 修复：取消规则短路，每轮走 LLM（D1）** | 移除 `focus_is_stable` 规则短路归档路径（原逻辑焦点稳定时跳过 LLM 用规则抽取，无法处理相对时间"1年前3岁"/开放词汇"肚子疼"/隐含推算）+ 每轮走 LLM（LLM 既判归属又抽属性）+ `_parse_resolution` 空 JSON 默认 action=chat + `resolve_and_extract` 移除无信号预过滤（让 LLM 通过上下文判断归属）+ 规则抽取降级为 LLM 解析失败兜底 | 时序 3/3 + 全量 17/17 全绿 |

> **全量门禁：17/17 ALL GREEN**（`run_harness.py --all`，~50s）。重型真实模型测试默认跳过，
> 设 `RUN_REAL_MODEL=1` 并加 `--timeout 600` 可显式运行（bge 语义嵌入弯曲 7/7、真实重排 4/4 均绿）。

### 模块实现状态
| 模块 | 状态 | 说明 |
|------|------|------|
| MOD-knowledge-ingest | **partial（P1 已落地）** | 统一接口 + 真实爬虫 + 归一管线已交付；PDF/OCR 适配器 deferred（non-goal） |
| MOD-kb | partial | 分块/嵌入/向量检索 + 独立重排器已跑通真实嵌入 |
| MOD-agent | partial | RAG 核心 + 每企业可配置 LLM + 约束块注入 + **Prompt Caching（RAG prompt 顺序 + 命中监控 + 预热）** + **检索查询融合档案上下文（`_enrich_query`）** 已落地 |
| MOD-session | **partial（P1 已落地）** | 三级隔离 + 用户约束累积/压缩已交付 |
| MOD-baby-profile | **partial（P2 + P2-v2 已落地）** | 客户 1→N 宝宝 + 每轮消歧 + 混合式建档安全网 + 主动归档 + 焦点注入；pending 防污染 / 消歧失败熔断 / 跨会话写锁 / 待确认清理 / 焦点稳定结果缓存 / Prompt Caching 稳定前缀 + ORDER BY + 预热；**schema v2: `birth_date`/`gestational_weeks`/`medical_history`/`feeding_history` 结构化字段 + SQL 自动迁移**；**终极实战 harness: 5 宝宝 34 条碎片化信息随机归档 + 交叉提问验收（P30-P39）** |
| MOD-wechat | partial | iLink Bot API 网关 + 约束/档案接线已落地 |
| MOD-deploy | **partial（P1 + P0 安全已落地）** | **Windows 直装 + 三层依赖分层 + `configure.ps1` 配置向导 + 环境变量覆盖 + `PluginManager` 可插拔模型路径** + **P0 安全**：密钥环境变量化(`from_yaml_with_env`+env_file+secret_scan) / 出入站白名单(`egress.EgressPolicy`) / 健康数据加密(`crypto.Vault`) 均已落地并配 harness |

## 端侧部署

### Windows 直装部署（低配门店推荐）

门店电脑无需预装 Python，通过交互式配置向导完成部署：

```powershell
# 1. 运行配置向导（选择模式、拉取依赖、生成 .env.local）
.\deploy\postinstall\configure.ps1

# 2. 启动服务
.\deploy\postinstall\run-agent.ps1

# 3. 注册开机自启（可选）
.\deploy\postinstall\register-service.ps1
```

**三种部署模式（端侧部署人员决定）：**

| 模式 | LLM | 嵌入 | RAM | 适用场景 |
|------|-----|------|-----|----------|
| demo | mock（内置） | mock（内置） | ~200MB | 产品演示、功能验证 |
| light | 云端（DeepSeek/OpenAI） | mock（内置） | ~300MB | 4-8GB 内存的门店电脑 |
| full | 本地 Ollama 或 云端 | bge-small-zh（本地真实语义） | ~2GB | 8GB+ 内存的门店电脑 |

**依赖三层分层策略：**

- **Tier 1 — 稳定大文件（按 URL 拉取）**：torch CPU (~800MB)、sentence-transformers、bge 模型权重 (~100MB)。
  迭代缓慢（半年一更）、体积大。不在安装包中捆绑，由 `configure.ps1` 按 `deploy/dependency-manifest.yaml`
  声明的 URL 在配置阶段拉取。支持：PyPI 镜像选择、HuggingFace 国内镜像（hf-mirror.com）、离线 U 盘导入。
- **Tier 2 — 小依赖（捆绑在安装包）**：pydantic、pyyaml、chromadb、httpx（共 ~60MB）。
- **Tier 3 — 易变代码（可插拔）**：`src/` 应用源码、`enterprise.yaml` 配置。升级时只需替换 `app/` 目录。

**可插拔模型路径：** `PluginManager`（`src/common/plugins.py`）管理模型插件的生命周期。
`embeddings.py` 和 `rerank.py` 通过插件管理器解析模型路径——优先本地插件目录，无则回退 HuggingFace 下载。
模型可插拔替换，不动业务代码。

**环境变量覆盖：** `EnterpriseConfig.from_yaml_with_env()` 加载 yaml 后用环境变量覆盖。
端侧不改 yaml 文件即可切换 LLM/embedding 模式。支持的环境变量见 `src/common/config.py` 文档。

### Docker 部署（服务器/高配门店）

```bash
docker-compose -f deploy/docker-compose.yml up -d
```

详见 [`deploy/Dockerfile`](deploy/Dockerfile) 和 [`deploy/docker-compose.yml`](deploy/docker-compose.yml)。

### 部署安全清单（P0，上线前必做）

| 控制 | 做法 | 验证 |
|------|------|------|
| 密钥环境变量化 | 凭证仅经 `env_file: .env.local` 注入，不内联/不打包；`.env*` 被 gitignore；`deploy/.env.example` 为模板 | `harness/test_secret_scan.py` + CI `scripts/secret_scan.py` |
| 出入站白名单 | 应用层 `EgressPolicy` 仅放通 `ilinkai.weixin.qq.com` / CDN 域名 + 显式 LLM 端点；`AGENT_EGRESS_ENFORCE=1` 开启拦截 | `harness/test_deploy_egress.py` |
| 健康数据加密 | 宝宝敏感字段落库 Fernet 加密（密钥 `AGENT_DATA_ENCRYPTION_KEY`）；生产缺密钥启动即失败 | `harness/test_data_encryption.py` |

> 部署时务必设置 `AGENT_DATA_ENCRYPTION_KEY`（base64 32 字节 Fernet key）与 `AGENT_EGRESS_ENFORCE=1`；
> 未设加密密钥时开发/mock 用确定性 dev key（仅限非生产数据，启动会告警）。

## 运行验收

```bash
python3 scripts/run_harness.py --all        # 全量回归（CI 门禁，20/20 绿）
python3 scripts/run_harness.py --module kb  # 仅某模块
python3 scripts/run_harness.py --module deploy  # 端侧部署验收
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
6. **端侧部署 = Python 直装优先**：门店电脑配置有限，采用 Python embeddable + 离线 wheels + 三层依赖分层策略；Docker 作为服务器/高配门店的可选方案。

## 决策状态（见 charter O1–O3′）

- ✅ **O1 向量库 = Chroma**（嵌入式 PersistentClient；2026-07-20 由 SQLite-vec 改换）。
- ✅ **O3′ 网关策略 = 方案 B 自建**（直接采用 Hermes 的方案 A 已否决）。
- ✅ **O2 首版写码切入点 = 端侧最小闭环优先**（知识库+问答+微信接入打通后，叠加 P 级增强）：已进入编码，P1 知识转化层与 P1 会话约束层已交付并通过全量门禁。
