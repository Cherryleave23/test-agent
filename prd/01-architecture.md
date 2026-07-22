# 总体架构（Architecture）

> 来自「框架分析」阶段（Principle P2）的显式结论。架构决策须落纸面，实现前不得悬空。
> 本文件随模块增删而更新；PRD 冲突时以 `00-charter.md` 为准。
> 修订：v3 据源码精读 + 用户决策定稿——微信=个人微信官方 iLink Bot API（**自建轻量网关，方案 B**，C5/O3′）；
> 向量库=**Chroma（O1，2026-07-20 由 SQLite-vec 改换）**；LLM 每企业可配置（C2）；知识来源=PDF/图片表格/爬虫（C3）。

## 技术栈

| 层 | 选型 | 理由 |
|----|------|------|
| 语言 / 框架 | Python 3.11 + FastAPI | AI/RAG 库最成熟；FastAPI 异步适配多员工并发 |
| 知识采集（standalone 工具 `tools/dataproc/`） | 标准库 `urllib`+`html.parser`（爬虫，零依赖）/ PaddleOCR + PP-Structure（PDF 解析与扫描件 OCR + 表格识别，端侧可选安装）；产出 **NDJSON bundle 产物契约** | 来源为 PDF/图片表格/官网网页，均为非结构化（C3）；工具与 agent **彻底隔离**（可单独在某台设备运行） |
| 统一接口 | 自定义 `UnifiedKnowledgeSource` 适配器协议（Python Protocol） | 把不同来源归一为同一知识记录结构 |
| 向量库 | **Chroma**（嵌入式 PersistentClient，已选 O1，2026-07-20 由 SQLite-vec 改换） | 原生 metadata 过滤强化企业隔离；与 SQLite FTS5 做 RRF 混合检索；嵌入式契合端侧 1 实例 |
| 嵌入模型 | 可配置（默认 bge-small-zh / 本地） | 中文母婴语料适配 |
| LLM | **每企业可配置** provider：Ollama（端侧本地）/ 云 API | 由企业部署策略决定（C2），非写死 |
| 会话存储 | SQLite + 文件（单实例内）/ 可选 Redis | 会话状态、历史按 (企业×员工×会话) 隔离 |
| 微信接入 | **自建 iLink Bot API 网关**（参考 Hermes `weixin.py`，方案 B） | 腾讯官方协议、HTTP 长轮询；员工=`from_user_id`；不耦合 Hermes 运行时（C5/O3′） |
| 部署 | Docker + docker-compose，config 驱动；**实例含 iLink bot 凭证，运行期出网 HTTPS** | 1 家 1 实例（C1/G6） |
| 验收 | `scripts/run_harness.py`（自包含 runner） | controlled-vibe-coding 铁律：真实运行判 PASS/FAIL |

## 目录结构（目标形态）

```
/workspace
├── prd/                      # 本文档体系（单一事实源）
│   ├── 00-charter.md
│   ├── 01-architecture.md
│   ├── 02-index.md
│   └── modules/
├── harness/                  # 验收脚本（只增不删）
│   └── test_*.py
├── scripts/
│   └── run_harness.py        # 自包含确定性 runner（不在 harness/ 内，避免自发现）
├── tools/
│   └── dataproc/             # ⚠️ standalone 数据处理工具（独立 pyproject，严禁 import src.*）
│       ├── adapters/         #   crawl / pdf / image_table OCR（MOD-knowledge-ingest P2）
│       ├── structurer.py     #   结构化抽取（LLM，工具自带 provider）
│       ├── resolver.py       #   商品实体解析（reg_number/元组/HQ/pending）
│       ├── classifier.py     #   分类（ptype / product_category）
│       ├── schema.py         #   产物契约 schema（ProductRecord/CorpusRecord/HQProductRecord）
│       └── cli.py            #   dataproc build/crawl/ocr → 产出 NDJSON bundle
├── src/                      # 实现代码（agent 端运行时，按模块分包）
│   ├── ingest/               # MOD-knowledge-ingest（agent 侧）：importer.py 加载 bundle → store
│   ├── kb/                   # MOD-kb
│   ├── agent/                # MOD-agent
│   ├── session/              # MOD-session
│   ├── wechat/               # MOD-wechat
│   └── deploy/               # MOD-deploy
└── README.md
```

## 模块依赖图

```
                 ┌─────────────────────────── standalone 工具 tools/dataproc/ ───────────────────────────┐
                 │ crawl / OCR / 结构化抽取 / 实体解析 / 分类  ──产出──▶  NDJSON bundle（产物契约）          │
                 └───────────────────────────────────┬───────────────────────────────────────────────────┘
                                                      │ 产物文件（唯一共享契约；零代码依赖）
                                                      ▼
MOD-wechat ──接收消息/身份(from_user_id)──▶ MOD-session ──取/存会话──▶ MOD-agent ──检索──▶ MOD-kb ◀──写入── src/ingest/importer（加载 bundle，含向量化）
     │                                 │                      │                                      │
     └──回复消息◀─────────────────────┘                      └──────────企业定制 prompt──────────────┘
                                                  MOD-deploy 包裹以上全部（1 家 1 实例，含 iLink bot 凭证）
```

依赖方向单向、无环：
- **数据处理工具 `tools/dataproc/` 与 agent 端彻底隔离**：工具不 `import src.*`、agent 不 `import` 工具；二者**仅以 NDJSON bundle 产物契约为共享边界**。工具可单独在某台设备运行，产出 bundle 后（经 U 盘/内网/对象存储）交付端侧实例。
- 采集写入经 **agent 端 `src/ingest/importer` 加载 bundle → 写入知识库（kb，含向量化/索引）**；importer 是 agent 运行时内唯一把产物写入 kb 的入口，自身不做 OCR/结构化/解析、不调用 LLM。
- 知识库（kb）被 agent 检索，自身独立。
- agent 依赖 kb 与 session 提供的上下文，不反向依赖 wechat。
- session 依赖 agent 产出，但对外只暴露「按会话隔离的问答」契约。
- wechat 是最外层适配器，依赖 session 的契约，不含业务；身份来源为 iLink 消息的 `from_user_id`（C1）。
- deploy 是部署包装，不进入模块运行时依赖；仅需注入 iLink bot 凭证并放通出网 HTTPS（C1/G6）。

## 关键决策与理由

- **D1 微信 = 个人微信（官方 iLink Bot API）**：用户明确选个人微信（C1），且源码确认走腾讯官方
  iLink Bot API（非 wechaty 逆向协议）。多名员工各自个人微信向同一 bot 账号发消息，以 iLink 消息中的
  **`from_user_id`** 作为 `employee_id`；传输为 **HTTP 长轮询（getUpdates）**，登录为扫码（qr_login）。
  封号风险显著低于协议方案（O3 已消解）。
- **D2 端侧向量库 = Chroma（已选 O1，2026-07-20 由 SQLite-vec 改换）**：嵌入式 `PersistentClient`，
  每实例一个持久化目录即企业隔离；原生 metadata 过滤（按 `enterprise_id`）强化隔离；
  向量召回与 SQLite FTS5 关键词召回做 RRF 融合。
- **D3 LLM 每企业可配置（已确认 C2）**：母婴企业数据敏感性不一，有的只允许端侧推理，有的接受云 API；
  用 provider 抽象 + 配置开关，避免后期重写；**数据是否出网由企业配置决定**。
- **D4 会话隔离键设计**：会话主键 = `(enterprise_id, employee_id, conversation_id)`，
  其中 `employee_id` = iLink 消息的 `from_user_id`（C1）；借鉴 Hermes `build_session_key` 的
  `agent:main:weixin:dm:<user_id>` 思路自研，保证多员工互不串扰。
- **D5 知识统一接口先行**：采集源（网页/PDF/图片表格）归一为同一 `KnowledgeRecord` 结构再入库；
  **首版不含结构化 API 适配器**（C3），后续如需商品库/ERP API 再扩充。
- **D6 验收强制 harness**：每个模块的可验证行为都必须有可执行脚本，见 `harness/`，全绿才算完成。
- **D7 端侧实例的微信约束大幅放宽**：iLink Bot API 是标准 bot API——bot 账号经**扫码登录（qr_login）**
  后，运行期仅为**出网 HTTPS 长轮询**（无需常驻微信桌面客户端/协议网关）。因此 MOD-deploy 的
  「1 家 1 实例」主要是 Agent 服务 + 一个 iLink bot 凭证，部署约束与常规后端服务相当。

- **D8 网关 / agent 核心采用方案 B（自建，不耦合 Hermes）**：仅把 Hermes `weixin.py` 当作 iLink Bot API 的
  **参考实现**——借其端点、`ilink_bot_token` 鉴权头、`context_token` 回带、`sync_buf` 续传游标、限流熔断、
  配对/绑定思路；**微信网关、会话隔离、RAG agent 核心全部自研**（C5/O3′）。代价是需复刻 Hermes 已验证的
  那部分能力，但换来完全解耦、可控、可端侧独立部署。

- **D9 数据工具与 agent 彻底隔离（standalone `tools/dataproc/`）**：用户明确「数据抽离工具和 agent 是彻底隔离的工具」——
  数据处理工具可单独在某台设备运行，把众多产品/知识处理成 agent 端需要的数据结构。据此，采集 / OCR / 结构化抽取 /
  商品实体解析 / 分类全部落在 `tools/dataproc/`（独立 pyproject、零反向依赖），产出**语言中立的 NDJSON bundle（产物契约）**；
  agent 端仅 `src/ingest/importer.py` 负责把 bundle 载入 `store`（含向量化/索引）。双方的**唯一**共享物是产物 schema，
  故可各自独立演化、独立部署、独立测试（边界集成测试 `IMP6` 横跨两上下文作为隔离正确性证据）。
  embedding/索引在 agent 侧做（模型与运行时在 agent 端），工具不持有 agent 的 embedding/LLM 凭据。
