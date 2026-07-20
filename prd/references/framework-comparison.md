# 框架对比：我们的母婴 Agent vs Hermes 框架

> 目的：把「我们的自研 Agent（方案 B）」与「Hermes 框架」在架构、会话、微信接入、知识/数据、部署五个层面做显式对比。
> 结论先行：Hermes 是**通用型、自进化、多平台**的 Agent 框架（参考实现来源）；我们的 Agent 是**母婴垂类 ToB、端侧 1 企业 1 实例、带结构化 B 端产品库**的定制产品。二者**只在 iLink 微信契约层面借鉴 Hermes（C5/O3′）**，运行时完全解耦。

---

## 一、本质定位差异

| 维度 | Hermes | 我们的母婴 Agent |
|------|--------|------------------|
| 产品形态 | 通用 AI Agent 框架/运行时，个人/开发者自用 | 面向母婴企业的**垂直领域产品**，交付给门店使用 |
| 领域知识 | 无内置领域知识，靠 skills + 通用 LLM | 深度内置母婴育儿/营养/产品（奶粉/尿不湿/营养品）结构化知识 |
| 部署形态 | 个人主机 / 云端单用户运行 | **1 家企业 1 个端侧实例**，企业间数据/配置隔离（NG1） |
| 多租户 | 单用户多平台（Telegram/微信/Discord…） | **单企业多员工**（同一 bot 账号，按 `from_user_id` 隔离） |
| 自进化 | 有 memory/skill 回顾、可自我改进 | 无自进化；知识变更走受控的 ingest 流程（可控、可审计） |
| 与我们的关系 | **iLink 微信契约的参考实现** | 仅借其微信协议实现，**不耦合其运行时** |

---

## 二、架构分层对比

| 层 | Hermes | 我们的 Agent（模块） |
|----|--------|----------------------|
| 接入网关 | `gateway/platforms/*` 多平台适配器（微信仅其一，20+ 平台） | `MOD-wechat`：仅个人微信，专注 iLink Bot API（参考 Hermes `weixin.py`） |
| 会话管理 | `gateway/session.py`：单文件 JSON/`state.db`，`build_session_key` 多维隔离 | `MOD-session`：SQLite `sessions`+`turns`，主键 `(enterprise_id, employee_id, conversation_id)` |
| 配对/授权 | `gateway/pairing.py`：DM 配对码授权（OWASP/NIST） | `MOD-wechat` + 企业级员工绑定（借鉴 `dm_policy`，自研按实例） |
| Agent 核心 | `agent/conversation_loop.py`：工具分发、重试、压缩、skills、memory | `MOD-agent`：6 步 RAG 管线，企业定制 prompt，引用/防幻觉/母婴免责 |
| 知识层 | 无结构化领域知识库；靠网页/文件 skills 临时检索 | `MOD-kb`：**三库模型**（总部知识库 + 总部商品库 + B 端结构化产品表） |
| 知识采集 | 通用文件/shell/网页工具 | `MOD-knowledge-ingest`：爬虫 + PDF/OCR + 图片表格 + Excel/CSV 导入 |
| 向量库 | 未内置（依赖外部检索/工具） | **Chroma**（嵌入式 PersistentClient，metadata 过滤强化企业隔离，O1） |
| LLM | 多 provider 适配（Anthropic/Bedok/Gemini/Azure/Codex…） | 每企业可配置（Ollama 端侧 / 云 API，C2） |
| 部署 | 个人运行 + ACP 适配层（远程客户端） | `MOD-deploy`：Docker/compose，1 企业 1 实例，含 iLink bot 凭证 |

---

## 三、会话隔离机制对比（关键）

**Hermes**（`gateway/session.py:904` `build_session_key`）：
- 键格式 `agent:<profile>:<platform>:<chat_type>:<id>`，默认 namespace `agent:main`。
- DM 按 `chat_id`（私聊）或 `user_id`（无 chat_id 时）隔离，群组按 `chat_id` + `user_id` 隔离，线程可共享。
- 多 profile 命名空间用于同一进程 multiplex 多个 agent 实例。
- 对用户 ID 做了 **SHA-256 哈希脱敏**（`_hash_sender_id` → `user_<12hex>`）。
- 落地：JSON 文件 + `~/.hermes/state.db`（SQLite），`get_or_create_session` 带 1 小时 freshness 窗口自动续接。

**我们**（`MOD-session`）：
- 键 = `(enterprise_id, employee_id, conversation_id)`，`employee_id` = iLink `from_user_id`。
- **多了一级 `enterprise_id`**：因为我们的隔离单位是「企业 × 员工 × 会话」，而非 Hermes 的「平台 × 用户」。
- 每会话 `asyncio.Lock` + flight 去重 + `to_thread` 落盘，避免并发串话。
- 用 SQLite 表而非 JSON 文件，便于与企业数据、知识库同库事务管理。

> 借鉴点：键构造思路（`agent:main:weixin:dm:<user_id>` 的「按发送方隔离」）、配对/绑定的安全考量。
> 差异点：我们强约束 `enterprise_id` 一级（多租户物理隔离 vs Hermes 单用户多平台）；不做 user_id 哈希（端侧、企业自有数据，无 PII 外泄风险，且需可审计溯源）。

---

## 四、微信接入对比

| 项 | Hermes `WeixinAdapter` | 我们的 `MOD-wechat` |
|----|------------------------|---------------------|
| 协议 | 腾讯官方 iLink Bot API（HTTP 长轮询） | 同（参考其实现） |
| 鉴权 | `AuthorizationType: ilink_bot_token` + `Bearer` token + `iLink-App-Id: bot` | 同 |
| 续传 | `sync_buf` 游标 | 同（自研维护） |
| 对话连续 | `context_token` 回带 | 同 |
| 登录 | `qr_login` 扫码 + `get_qrcode_status` 轮询 | 同 |
| 去重/限流 | `message_id` 去重、限流熔断 | 同（自研） |
| 多员工 | 靠 `from_user_id` 自然隔离（1 bot = 1 实例内多用户） | 同，且叠加企业绑定白名单 |
| 平台广度 | 仅是 20+ 平台之一 | 唯一平台，做深做专 |

> 核心澄清：Hermes 微信并非「1 对 1」。**1 对 1 只在 bot 账号层**（1 个 iLink bot = 1 个网关实例）；在实例内部，它本就支持**同一 bot 被多名用户私聊、各自独立会话**——这正是我们「1 agent 多员工」所需的模型。故 Hermes 既消解了封号风险（官方协议），也验证了多员工隔离可行。

---

## 五、知识 / 数据层对比（最大差异）

**Hermes**：通用框架，**无领域知识库**。它的「知识」来自运行时 skills、临时文件读取、网页检索——不沉淀结构化产品数据，也不区分「总部知识」与「企业私有数据」。

**我们**：这是母婴 ToB 的立身之本，因此有**三库模型**（`references/data-model.md`）：
1. **总部知识库**（HQ KB）：厂商维护、随分发内置，所有门店共享的育儿/营养通用知识（Chroma 向量，`enterprise_id='hq'`）。
2. **总部商品库**（HQ Product Lib）：厂商侧维护的标杆商品数据，新客户 onboarding 时**拼接复用**，缩短冷启动。
3. **B 端数据库**（B-end DB）：每企业私有、彼此隔离的结构化产品表（奶粉 14 必填字段、营养品、尿不湿、独特服务）。

> 这是与 Hermes 最本质的区别：我们卖的不是「一个会聊天的通用 agent」，而是「带该企业全部产品知识、且知识可信可溯源的垂类顾问」。

---

## 六、能力取舍总结

| 能力 | Hermes 有 | 我们有 | 说明 |
|------|-----------|--------|------|
| 多平台 | ✅ 20+ | ❌ 仅微信 | 我们聚焦母婴门店主渠道，不做平台发散 |
| 自进化 / 自我改进 | ✅ memory+skill 回顾 | ❌ | 企业场景要可控可审计，知识变更走 ingest |
| 通用工具调用 | ✅ shell/文件/网页 | ⚠️ 受限 | 我们只在 RAG 内做受控检索，不开放任意代码执行 |
| 结构化领域知识 | ❌ | ✅ 三库模型 | 核心壁垒 |
| 企业级隔离 | ❌（单用户） | ✅ enterprise_id 一级 | 端侧 1 企业 1 实例 |
| 端侧轻量 | ⚠️ 依赖重 | ✅ Chroma 嵌入式（单目录即一企业库） | 适合门店边缘部署 |
| 母婴合规 | ❌ | ✅ 免责+内容安全 | 婴幼儿健康建议边界 |

---

## 七、我们借鉴了什么、没借什么

**借鉴（参考实现，非依赖）**：
- iLink Bot API 的端点、`ilink_bot_token` 鉴权头、`context_token` 回带、`sync_buf` 续传游标、限流熔断、配对/绑定安全考量（C5/D8）。
- `build_session_key`「按发送方隔离」的思路 → 演化为我们的 `(enterprise_id, employee_id, conversation_id)` 三级键。

**没借（自研 / 不引入）**：
- Hermes 运行时、多平台网关、ACP 适配层、多 LLM 适配全家桶、skills 自进化机制——全部不耦合。
- 通用 agent loop（工具分发/重试/压缩）不套用，改用**确定性的 6 步 RAG 管线**，更适合母婴问答的可控性与可引用性。

---

## 八、结论

> Hermes 是一个**优秀的「通用 Agent 框架」开源参考**，尤其在个人微信（iLink）接入上给了我们经过验证的协议实现。
> 但我们的产品目标是**母婴垂类 ToB 的可信顾问**：差异不在「谁的 agent 更聪明」，而在**领域知识沉淀（三库模型）、企业级隔离、端侧可部署、母婴合规**这四件事上。
> 因此策略是：**微信契约照抄 Hermes（省去协议踩坑），其余全部自研（方案 B）**，做到既可独立端侧交付，又完全可控、可审计、可溯源。

---

*附录：本对比基于 Hermes 源码精读（`hermes-wechat/references/weixin.py`、`hermes-agent/gateway/{session,pairing}.py`、`hermes-agent/agent/conversation_loop.py` 等）与本项目 PRD（`00-charter.md`、`01-architecture.md`、各 `modules/MOD-*.md`）。*
