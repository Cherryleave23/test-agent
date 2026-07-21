# MOD-baby-profile 模块详解（宝宝/客户档案层 · 主动建档与归档）

> 依据 charter 真实场景诉求：企业部署后，**一名员工对应多名客户、每个客户对应多个宝宝**，且员工常在对话中
> **快速切换**当前在聊的宝宝（A 客户宝宝 → B 客户宝宝 → 提一嘴 C → 回 A → 再问产品知识）。
> 需要：① 一个按 `(企业×员工)` 隔离的宝宝/客户档案库；② Agent **主动意识到**当前在聊哪个宝宝；
> ③ 把对话中抽到的宝宝属性**自动归档**进对应档案；④ 不把「别人的/假设的」宝宝误建档。
> 本文件为**可实现规格**，已进入编码并按此落地、配齐 harness。

## 职责
为母婴导购场景提供「宝宝/客户档案」持久层与每轮消歧/归档编排：员工切换在聊的宝宝时，Agent 能正确定位、
把明确属性累积进正确档案，并可选地把「当前焦点宝宝档案」注入 system prompt 以指导回答；跨员工/跨企业不可见。

---

## 〇、本次实施 P2（扩展：宝宝/客户档案层）

> 分类：**P2 扩展**。源自身处一线的真实部署诉求（多客户多宝宝 + 快速切换 + 主动归档）。
> 设计经四轮 AskUserQuestion 收敛：① 每轮 LLM 实体链接；② 每轮抽取 upsert + 自动建档；
> ③ 混合式建档 + 待确认安全网；④ 建 customer 表（客户 1→N 宝宝）。

### 意图（must-have）
1. **数据模型 `Customer`(1→N) `BabyProfile`**：客户为独立的 1→N 实体（决策④）；宝宝字段复用 `MilkProduct`
   词表（`stage`/`age_range`→`baby_age`/`price`→`budget`/`brand`→`brand_preference`/`ptype`→`category`），
   含 `status`（pending 待确认 | confirmed）。含注入 prompt 块与 JSON。
2. **每轮消歧 `resolve_and_extract`**：把「近期上下文 + 当前消息 + 该员工已知 客户→宝宝 清单 + 本会话焦点宝宝」
   喂给 LLM，输出当前在聊哪个宝宝（实体链接，处理快速切换/代词指代）、抽取到的宝宝属性、以及是否关于真实管理
   的宝宝（第三人称/假设则不建档）。复用 `UserConstraints` 的 `merge` 语义做属性累积。
3. **短路优化**：当前消息无宝宝相关信号（且未提到已知宝宝/客户名）时跳过 LLM 调用，沿用焦点、规则抽取——省成本。
4. **混合式建档安全网 `resolve_and_archive`**：
   - 第三人称/假设 → **不建档、不归档**（绝不污染真实客户档案）。
   - 全新宝宝 → **自动建档但 `status=pending`**（待员工确认/修正，安全网）。
   - 已建档宝宝 → 抽取到的明确属性 **upsert 累积**（主动归档，跨轮保留）。
   - 防重复建档：同 `(企业×员工×宝宝名)` 已存在则复用，不新建。
5. **持久化 `BabyProfileStore`**：`customers` + `babies` 两表，按 `(enterprise_id, employee_id)` 隔离；
   `get_or_create_customer` / `create_baby` / `upsert_baby_attrs`(merge) / `mark_confirmed` /
   `merge_baby`(自然语言修正合并并删源) / `delete_baby` / `list_for_employee`(注入 LLM 上下文)。
6. **焦点宝宝 `SessionStore.session_baby_focus`**：本会话当前焦点宝宝（代词/快速切换消歧锚点）；
   `get/set_focus_baby`。
7. **网关接线 `wechat/gateway.handle_message`**：每轮在约束步骤后调用 `resolve_and_archive`，设焦点，
   并把焦点宝宝档案块注入答案。
8. **注入 `Agent._build_messages`**：接受 `baby_block`，注入 `【当前宝宝档案】`；不传则行为与历史一致（向后兼容）。
9. 每行为配 harness（CVC 只增不删）。

### 非目标（non-goals，本次不做）
- 不做员工级长期记忆/跨会话宝宝生命周期分析（超出 P2）。
- 不做自动删除待确认档案的定时清理（留待后续；pending 状态由员工显式修正/合并/删除）。
- `merge` 跨名合并需源/目标双名，当前消歧仅给目标；无可靠源时**退化为 upsert 到目标**，不做危险自动合并。
- 不破坏既有绿测试：`MockProvider` 忽略 system prompt → 注入档案块不改 Mock 回答。

### 文件与 harness 落点
| 文件 | 动作 | 说明 |
|------|------|------|
| `src/baby/models.py` | 新 | `Customer` + `BabyProfile`（`merge`/JSON/`to_prompt_block` 复用 `UserConstraints` 语义） |
| `src/baby/store.py` | 新 | `BabyProfileStore`：`customers`+`babies` 表、`(ent,emp)` 隔离、upsert/confirm/merge/delete |
| `src/baby/resolution.py` | 新 | `resolve_and_extract`：每轮 LLM 实体链接 + 抽取；短路；JSON 解析失败兜底退化 |
| `src/baby/archive.py` | 新 | `resolve_and_archive`：消歧 + 混合式建档安全网 + 主动归档 + 设焦点 |
| `src/session/store.py` | 改（增） | 增 `session_baby_focus` 表 + `get/set_focus_baby` |
| `src/common/config.py` | 改（增） | 增 `baby_profile_enabled` / `baby_db_path`（默认开、复用实例库） |
| `src/wechat/gateway.py` | 改（增） | 每轮 `resolve_and_archive` + 设焦点 + 注入档案块 |
| `src/agent/pipeline.py` | 改（增） | `_build_messages` 接受 `baby_block` 注入 `【当前宝宝档案】`（向后兼容） |
| `harness/test_baby_profile.py` | 新 | `@module baby`：P1/P7/P8(存储) + P4/P5(消歧) + P2/P3(建档安全网/归档) + P6/P9(注入/兼容) |

> 状态：**done（P2 + 优化 B/C + Prompt Caching 全阶段）**。默认全量门禁 9/9 ALL GREEN（含本模块 `test_baby_profile.py` 22/22）。
> `02-index.md` 中 MOD-baby-profile 由 `backlog` 升级为 `partial`。

### P2 harness 验收表（`harness/test_baby_profile.py`，`@module baby`）
| 编号 | 验收点 | 对应实现 | 结果 |
|------|--------|----------|------|
| P1 | 显式建档：客户 + 宝宝创建且属性落库 | `store.py` | PASS |
| P7 | `(ent,emp)` 隔离：跨员工/跨企业不可见 | `store.py.list_for_employee` | PASS |
| P8 | 持久化全链路：upsert(merge)/confirm/merge/delete round-trip | `store.py` | PASS |
| P4 | 快速切换意图消歧：A壮壮→B妞妞→回A，每轮解析正确焦点 | `resolution.py` | PASS |
| P5 | 代词指代（他/宝宝→焦点）+ 无信号短路不调 LLM | `resolution.py` | PASS |
| P2 | 混合式建档安全网：全新宝宝自动建档(pending) / 第三人称不建档 / 不重复建档 | `archive.py` | PASS |
| P3 | 主动归档跨轮累积：抽取属性 upsert 进正确宝宝、跨轮保留并累加 | `archive.py` | PASS |
| P6 | 焦点宝宝档案块注入 system prompt（`【当前宝宝档案】`） | `pipeline.py._build_messages` | PASS |
| P9 | 向后兼容：不传 baby_block 无档案块；既有用户约束块注入不受影响 | `pipeline.py._build_messages` | PASS |
| P10 | **pending 防污染**：同名跨客户不误合并（旧 pending 不被新真实宝宝复用） | `store.find_baby_by_name`(仅 confirmed) + `_match_known`(客户,宝宝)精确优先 | PASS |
| P11 | 同名多客户歧义：未给客户名时不自动匹配（防跨客户误配） | `resolution._match_known` | PASS |
| P12 | 过期待确认清理：`prune_stale_pending` 只删陈旧 pending，confirmed 不动 | `store.prune_stale_pending` | PASS |
| P13 | 消歧失败可观测：`parse_failed` 标志 + 兜底沿用焦点不崩 | `resolution` + `archive` | PASS |
| P14 | 网关级连续失败熔断：≥阈值降级为仅产品问答，不再建档/归档 | `gateway` + `session_resolution_fails` | PASS |
| P15 | 跨会话写锁：并发 upsert 同一宝宝不丢失更新 | `store._baby_locks` | PASS |
| P16 | 焦点稳定结果缓存：跳过 LLM 消歧（规则归档）+ 提及他宝仍触发切换 | `resolution.focus_is_stable` + `archive` 缓存路径 | PASS |
| P17 | **Prompt Caching**：稳定前缀（指令+known）置首且跨轮一致（缓存命中契约）+ `cache_control` 断点（Anthropic 显式 / OpenAI 自动） | `resolution` 前缀分离 + `providers._apply_cache_control` | PASS |
| P18 | 消歧 prompt 结构：system = 稳定指令 + 已知清单（byte-for-byte 可缓存，可精确重建） | `resolution.resolve_and_extract` | PASS |
| P19 | 序列化稳定：同 `known` 两次 `json.dumps` 一致；顺序即缓存键（证明 `list_for_employee` 须 ORDER BY） | `store.list_for_employee` + `json.dumps` | PASS |
| P20 | `list_for_employee` 排序稳定：**SQL 加 `ORDER BY c.customer_id, b.baby_id`**（P0，缓存命中前提） | `store.list_for_employee` | PASS |
| P23 | 缓存预热：构造与消歧一致的稳定前缀并触发一次 provider 调用（`cache_control=True`） | `agent.warmup.warmup_prompt_cache` | PASS |

> 零破坏论证：`baby_block` 为可选形参、默认 `None`；网关仅在 `baby_profile_enabled` 且 `baby_store` 非 None 时接线；
> `MockProvider` 忽略 system prompt → 注入档案块不改 Mock 回答；既有 8 套测试不受影响（默认门禁 9/9 绿）。

### P2-v2 档案 schema 扩展 + 检索查询融合（2026-07-21）

> 来源：真实宝宝档案（共青二宝，14 个月，早产 35 周）模拟评估发现的 6 项缺陷中，修复前 4 项、推迟 2 项早产行为逻辑。

**修复（must-have）：**
- **Fix#1（P0）检索查询融合档案上下文**：`pipeline.answer()` 在调用 `store.retrieve()` 前，用焦点宝宝的 `baby_age`/`stage`/`allergens`/`brand_preference`/`category`/`health_notes`/`medical_history`/`feeding_history` 增强原始查询，提升 KB 命中率。新增 `_enrich_query(query, baby_profile)` 方法；`answer()` 新增可选 `baby_profile` 参数（向后兼容）。
- **Fix#2（P0）`gestational_weeks` 字段**：`BabyProfile` 新增 `gestational_weeks: Optional[int]`（孕周，如 35）。结构化存储，为后续矫正月龄计算预留。更新 `to_prompt_block()`/`merge()`/`is_empty_attr()` + SQL 迁移 + LLM 抽取 schema。
- **Fix#3（P0）`birth_date` 字段**：`BabyProfile` 新增 `birth_date: str`（ISO 格式，如 "2025-05-21"）。解决 `baby_age` 字符串随时间过期问题。同上更新全链路。
- **Fix#4（P1）`health_notes` 拆分**：`BabyProfile` 新增 `medical_history: List[str]`（医疗史，如 "早产35周" "出生5.18斤" "新生儿科9天"）+ `feeding_history: List[str]`（喂养史，如 "混合喂养→纯奶粉" "合生元派星3段" "非喷射性吐奶至4-5个月已缓解"）。保留 `health_notes` 字段向后兼容（自由文本兜底）。`to_prompt_block()` 优先展示结构化字段。

**推迟（deferred，记录待后续迭代）：**
- **Deferred#5（P1）system prompt 矫正月龄指令**：检测到 `gestational_weeks < 37` 时，在 system prompt 中追加「该宝宝为早产儿，辅食/营养建议请按矫正月龄（实足月龄 - (40 - 孕周) / 4）评估」指令。字段已就位（Fix#2），但行为逻辑推迟——需医学顾问审核指令措辞。
- **Deferred#6（P1）早产专项安全门**：检测到早产时追加「早产儿辅食添加需咨询儿科医生」专项告警（而非仅通用 DISCLAIMER）。推迟原因同上，需医学审核。

**harness 验收：**
| 编号 | 验收点 | 结果 |
|------|--------|------|
| P24 | `birth_date`/`gestational_weeks` 字段 round-trip | PASS |
| P25 | `medical_history`/`feeding_history` 列表 round-trip + merge 去重 | PASS |
| P26 | SQL 迁移：旧库 ALTER TABLE 自动加列 | PASS |
| P27 | `to_prompt_block()` 含新字段 | PASS |
| P28 | `_enrich_query()` 融合档案上下文 | PASS |
| P29 | `answer()` 传 baby_profile 时用增强查询检索 | PASS |

---

## 一、数据模型

### 1.1 客户 → 宝宝（1→N）
```text
Customer( customer_id PK, enterprise_id, employee_id, name, phone, notes )
BabyProfile( baby_id PK, enterprise_id, employee_id, customer_id FK,
             name, baby_age, gender, stage, allergens_json, budget,
             brand_preference_json, category, health_notes, status, ... )
```
- 客户为独立一等实体（决策④）：一名员工可管多名客户，每个客户可有多个宝宝。
- **隔离**：所有查询按 `(enterprise_id, employee_id)` 过滤，跨员工/跨企业天然不可见。
- `status`：`pending`（自动建档待确认）→ `confirmed`（员工确认/认可后）。

### 1.2 属性词表（复用 `MilkProduct`）
| 字段 | 来源词表 | 说明 |
|------|----------|------|
| `baby_age` | `age_range` | 月龄/年龄段 |
| `stage` | `stage` | 奶粉段位 |
| `allergens` | `allergens` | 过敏原（列表） |
| `budget` | `price` | 预算上限（非 None 优先） |
| `brand_preference` | `brand` | 品牌偏好（列表） |
| `category` | `ptype` | 品类倾向（奶粉/营养品/尿不湿） |
| `health_notes` | — | 健康/喂养备注 |

`BabyProfile.merge(other)` 复用 `UserConstraints.merge` 语义：新刷新旧、列表去重保序、budget 非 None 优先、
任一侧 `confirmed` 则保持 `confirmed`。

---

## 二、每轮消歧与抽取（`resolution.py`）

```
resolve_and_extract(history_text, current_msg, known, focus_baby_id, provider)
   │
   ├─ 短路：当前消息无宝宝信号 且 未提到已知宝宝/客户名 → 沿用焦点 + 规则抽取（不调 LLM）
   └─ 否则 一次 LLM 调用（Prompt Caching：稳定前缀置首 + cache_control=True）→ JSON
        ├─ 稳定前缀（messages[0].system，缓存对象）：指令 + known 清单（同员工跨轮一致）
        ├─ 每轮变量（messages[1].user，断点之后）：focus + history + current_msg
        ├─ action: chat|new_baby|new_customer|confirm|merge|delete
        ├─ customer / baby: 客户名 / 宝宝名（用于定位或新建）
        ├─ extracted: 宝宝属性对象（仅明确提到的）
        ├─ is_third_party / is_hypothetical: 别人的/假设的不建档
        └─ _parse_resolution：JSON 解析 + _match_known(known, customer, baby) 定位
             · 失败兜底：退化为规则抽取 + 沿用焦点（绝不抛错静默丢信息）
```

- **快速切换**：每轮独立消歧，严格依据「当前这句 + 上下文」判定当前在聊谁；提到已知宝宝名即走 LLM（不短路）。
- **代词指代**：`baby` 为空且 action 为 chat/confirm/merge/delete → 用本会话焦点宝宝兜底。
- **短路启发式**：固定关键词（奶粉/过敏/段位/客户/姐…）+ 已知宝宝名/客户名。提到已知名字必须消歧，不能短路。
- **Prompt Caching（优化 C，全阶段已落地）**：消歧 system prompt 在同员工会话中高度稳定（仅新增宝宝时变化），故把**稳定前缀**（指令 `_SYSTEM_INSTRUCTION` + 已知清单 `known`）作为首条 system 消息、开启 `cache_control`，使 provider 复用前缀、显著降低 input token 费用（DeepSeek 99% off / OpenAI 50% / Anthropic 90%）。`focus`/历史/当前句置于缓存断点之后——**切换焦点宝宝不破坏缓存**（焦点在变量区）。OpenAI 兼容端点靠自动前缀缓存（前缀已置首即生效，无需改写请求体）；Anthropic 端点由 `_apply_cache_control` 把 system 内容包成 content-block 并加 `cache_control: {type:"ephemeral"}` 显式断点。
  - **P0 前置（缓存命中前提）**：`list_for_employee` SQL 加 `ORDER BY c.customer_id, b.baby_id`，保证 `known_json` 序列化 byte-for-byte 稳定（P19/P20 守护）；否则顺序抖动会破坏前缀缓存。
  - **阶段4 预热（可选）**：`agent.warmup.warmup_prompt_cache` 构造与消歧一致的稳定前缀并触发一次调用，把前缀写入 provider 缓存，消除新会话首条请求的 cache-miss（仅当该员工预期 ≥2 次请求时值得）。
  - **可观测性（阶段3）**：`providers._report_cache_hit` 解析 `usage.prompt_tokens_details.cached_tokens` 并记录 `LLM 缓存命中: X/Y tokens (Z%)`，验证缓存实际命中。

---

## 三、建档安全网与主动归档（`archive.py`）

```
resolve_and_archive(store, provider, ent, emp, history_text, current_msg, focus_baby_id)
   │
   ├─ is_third_party / is_hypothetical → 安全网：不建档、不归档，沿用焦点
   ├─ 已匹配 known baby（baby_id 非 None）
   │     ├─ upsert 抽取属性（跨轮累积）
   │     ├─ confirm → mark_confirmed
   │     └─ delete → 删档案并清空焦点（若指向被删宝宝）
   ├─ 未匹配但 res.baby 非空 → 混合式自动建档（status=pending）
   │     ├─ get_or_create_customer（无客户名则用「（未命名客户）」）
   │     ├─ find_baby_by_name 防重复 → 已存在则复用
   │     └─ upsert 抽取属性
   └─ 兜底：沿用焦点（纯产品知识问句，无宝宝指向）
```

- **安全网（混合式 + 待确认）**：自动建档一律 `pending`，由员工后续确认/修正/合并/删除；第三人称/假设绝不落库。
- **主动归档**：抽取到的明确属性每轮 upsert 进正确宝宝，跨多轮保留并累加（如「6个月」→ 再「对牛奶过敏」→ 两者皆留）。
- **结果缓存（优化 B）**：`focus_is_stable` 判定焦点稳定（消息仅提焦点宝宝、无第三方提及、未提其他已知宝宝名）→ **跳过 LLM 实体链接**，仅用规则抽取把属性归档到焦点宝宝。LLM 仅做实体链接，属性抽取本就是规则，故质量无损却省一次 LLM 调用；提及任一「非焦点」已知宝宝名仍走 LLM 以检测快速切换。
- **Prompt Caching（优化 C）**：缓存未命中仍需发完整 prompt，但消歧 system prompt 在同员工会话中高度稳定，故把「指令 + known 清单」稳定前缀置首并标 `cache_control`，provider 复用前缀使 input token 费用降 50-90%；`focus` 等每轮变量置于断点之后，切换焦点不破坏缓存。与优化 B 互补：B 跳过调用、C 降低每次调用的 token 成本。

---

## 四、网关接线与 prompt 注入

- `wechat/gateway.handle_message`：约束步骤之后调用 `resolve_and_archive`，用返回焦点写 `session_baby_focus`，
  并取焦点宝宝档案（含客户名）生成 `baby_block` 传入 `agent.answer`。
- `agent.pipeline._build_messages`：在 system 末尾追加 `【当前宝宝档案】` 块（含宝宝名/客户/月龄/段位/过敏原/
  预算/品牌/品类/备注；pending 时附「待确认，有误请告知修正/合并/删除」）。块为空或不开功能则不注入（向后兼容）。

---

## 五、关键风险与缓解
| 风险 | 缓解 |
|------|------|
| 把别人的/假设的宝宝误建档 | `is_third_party`/`is_hypothetical` 安全网，不落库 |
| 快速切换时归错宝宝 | 每轮 LLM 实体链接 + 已知清单 `_match_known`(客户,宝宝)精确优先 + 焦点兜底 |
| 自动建档污染（重复/错客户） | `find_baby_by_name` 仅匹配 confirmed（pending 不自动复用）+ `_match_known` 同名歧义不误配 + pending 待确认安全网 |
| 跨员工泄露宝宝档案 | `(ent,emp)` 隔离 + `list_for_employee` 仅本员工 |
| LLM 输出解析失败 | 兜底退化为规则抽取 + 沿用焦点；`parse_failed` 标志 + 会话级连续失败熔断（≥3 轮降级仅产品问答并告警） |
| 跨会话并发写竞态 | `BabyProfileStore._baby_locks` 按 baby_id 串行化 upsert/merge/delete（仿 SessionStore 锁注册表） |
| 过期待确认累积 | `prune_stale_pending(days)` 清理陈旧 pending，confirmed 永不动 |
| 短路由成本 | 无信号短路，跳过 LLM 调用 |
| LLM input token 费用高（消歧每轮都发完整 prompt） | **Prompt Caching（优化 C）**：稳定前缀置首 + `cache_control`，provider 复用前缀降 50-90% input 费用；切换焦点不破坏缓存 |

---

## 六、注意事项 / 雷区
- **建档安全网是红线**：第三人称/假设绝不可落库；自动建档一律 `pending`，最终确认权在员工。
- 焦点宝宝是「代词/快速切换」消歧锚点，需每轮正确更新；员工显式切到新宝宝时即重置焦点。
- 档案块注入仅作「优先满足」提示，与知识库冲突时以知识库事实为准（块内已声明）。
- 复用 `MilkProduct` 词表，保证抽取/注入与既有 `UserConstraints` 语义一致，避免双套口径。
