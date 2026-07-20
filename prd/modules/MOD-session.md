# MOD-session 模块详解（多员工会话隔离）

> 依据 charter C1 / C4 / C5 / O3′：多员工共用同一 agent 实例，按 `(企业×员工×会话)` 三级隔离，单会话独立。
> **方案 B 自研**（不依赖 Hermes 运行时）；会话键设计**借鉴 Hermes `build_session_key`**
> （`agent:main:weixin:dm:<user_id>` 思路），但存储/并发/生命周期全部自研。
> 证据：`NousResearch/hermes-agent` 的 `gateway/session.py`（`SessionEntry` / `SessionStore` /
> `AsyncSessionStore` / `_SessionFlight` / `build_session_key`）。本文件为**可实现规格**。

## 职责
让**多名员工共用同一 Agent 实例**工作，而**每位员工的每个会话相互独立**：维护会话状态、
历史上下文与并发安全，对外只暴露「按会话隔离的问答」契约。与 MOD-wechat 的 `from_user_id` 直接配对。

---

## 一、会话模型（自研，借鉴 Hermes）

### 1.1 会话键
- `SessionKey = (enterprise_id, employee_id, conversation_id)`
- 字符串形态（借鉴 `build_session_key`）：`ent:<eid>:weixin:dm:<employee_id>[:<conversation_id>]`
- `employee_id` = iLink 消息的 `from_user_id`（C1）；`conversation_id` 缺省为 `default`（单会话场景）。

### 1.2 会话记录（Session）
| 字段 | 说明 |
|------|------|
| `session_key` | 路由键（见 1.1） |
| `session_id` | UUID，亦作 transcript 行键；文件名/路径必须经 `_is_path_unsafe` 校验防注入 |
| `enterprise_id` / `employee_id` / `conversation_id` | 三级隔离维度（eid 用于防御性 WHERE） |
| `turns` | 对话历史：`[{role:user|assistant, content, citations?, ts}]` |
| 生命周期标志 | `resume_pending` / `suspended` / `was_auto_reset` / `expiry_finalized`（借鉴 Hermes） |
| `model_override` | 按会话的 model/provider 覆盖（**仅 model/provider/base_url，绝不存凭证**） |
| `created_at` / `updated_at` | 时间戳 |

### 1.3 持久化（与 O1 一致：每实例 SQLite + Chroma）
- 会话表复用端侧单实例 SQLite 文件；向量检索走 Chroma（O1=Chroma），结构化/会话数据走 SQLite：
  - `sessions(session_key PK, session_id, eid, employee_id, conv, created, updated, flags_json)`
  - `turns(session_id, idx, role, content, citations_json, ts)`
- **企业隔离**：每实例一库即天然隔离；检索/查询再叠加 `WHERE eid=?` 防御纵深（即使同进程多企业也安全）。
- 路由索引：内存 `dict[session_key → session_id]`，启动时从 `sessions` 表载入，避免每消息查库。

---

## 二、对外契约 / 接口（自研）
- `SessionStore.key(eid, emp, conv) -> SessionKey`：构造三级隔离键。
- `SessionStore.get_or_create(key) -> Session`：取/建会话（含 flight 去重，见三.3）。
- `Session.append(role, content, citations=None)` / `Session.history() -> list[Turn]`：历史读写。
- `Session.reset(key)` / `Session.suspend(key)`：显式重置 / 挂起。
- `SessionStore.route(eid, emp, conv, msg) -> Answer`：组合 MOD-agent 完成一轮隔离问答（对外主入口）。

---

## 三、实现步骤
1. **SQLite schema**：`sessions` + `turns` 表；`eid`/`employee_id` 索引；`session_id` 路径安全校验。
2. **SessionKey + 路由索引**：实现键构造与内存路由表（启动载入、写时更新）。
3. **get_or_create + flight 去重**：并发首次加载同会话时，首加载者填充、其余 `await` 同一 future
   （借鉴 `_SessionFlight`），防止重复建会话。
4. **并发写安全**：每 `session_id` 一把 `asyncio.Lock` 串行化该会话读写；不同会话并发互不阻塞；
   所有 DB 写经 `asyncio.to_thread`（借鉴 `AsyncSessionStore`，不阻塞事件循环）。
5. **历史窗口**：超长历史按 token 预算截断，保留系统提示 + 最近 N 轮；截断前生成摘要，避免答非所问。
6. **生命周期**：闲置过期 / 显式 `/reset`（换新 `session_id`）/ 重启 `resume_pending`（保留同 `session_id`
   续上下文，借鉴 Hermes 重启恢复）。
7. **企业隔离**：所有查询带 `WHERE eid=?`；单实例单库为天然隔离层。

---

## 四、关键风险与缓解
| 风险 | 缓解（借鉴 Hermes） |
|------|----------------------|
| 跨员工泄露 | 三级键 + `WHERE eid` 防御纵深；单实例单库天然隔离 |
| 并发写乱/丢 | 每会话 `asyncio.Lock` + flight 并发加载去重 |
| 阻塞事件循环 | DB 写走 `asyncio.to_thread` |
| 重启丢上下文 | `resume_pending` 持久化，重启恢复同 `session_id` |
| 历史膨胀拖慢/答非所问 | token 预算截断 + 摘要；保留系统提示 |
| 会话键注入/路径遍历 | `session_id` 经 `_is_path_unsafe` 校验后拒绝 |
| 凭证泄露 | 会话存储**绝不**写 token/key；`model_override` 只存 model/provider |
| 多实例同 bot 互踢 | 由 MOD-wechat 平台锁保证；session 层按 `eid` 分区即可 |

---

## 五、harness 验收草案（真实运行，非自述）
> 用 mock MOD-agent（echo + 记录上下文）驱动，断言隔离/并发/生命周期。每个用例一个 `@session` 脚本。

- `test_session_isolation.py`：员工 A、B 各问，断言彼此历史互不串、各自独立。
- `test_session_multi_conv.py`：同员工两个 `conversation_id`，断言彼此独立。
- `test_session_concurrent.py`：对同一会话并发发 N 条 → 断言不丢不乱、顺序一致（flight+lock）。
- `test_session_history.py`：超长历史后仍能连续对话（截断+摘要生效）。
- `test_session_reset.py`：`/reset` 换新 `session_id`，旧历史隔离不可见。
- `test_session_resume.py`：模拟重启（带 `resume_pending`）→ 断言恢复同 `session_id`、上下文连续。
- `test_session_enterprise_isolation.py`：跨 `eid` 查询 → 断言无泄漏（WHERE 生效）。
- `test_session_key_injection.py`：构造恶意 `session_key`/`session_id` → 断言被拒（路径遍历防护）。

---

## 六、注意事项 / 雷区
- **隔离是红线**：任何路径都不得让会话键缺字段（尤其漏 `employee_id`），否则员工间对话泄露。
- 历史不可静默丢弃关键上下文导致答非所问；截断需留摘要。
- 会话数据属企业私有，端侧不对外同步（除非企业显式配置）。
- 会话存储不写任何凭证；模型覆盖只存 model/provider，凭据走运行时解析。
- 会话键设计借鉴 Hermes，但**实现完全自研**（方案 B），不 import Hermes。
