# MOD-agent 模块详解（Agent 服务核心 · 自研 RAG）

> 依据 charter C2 / C3 / C5 / O3′：基于 RAG 的母婴垂类问答；**企业定制 prompt** + **每企业可配置 LLM provider**
> （端侧 Ollama 默认 / 云 API 可切换）；**方案 B 完全自研**（不依赖 Hermes 运行时）。
> 本文件为**可实现规格**，进入编码时按此落地并配齐 harness。

## 职责
基于 RAG 的母婴垂类问答：从 MOD-kb 检索相关片段，结合**企业定制 system prompt**，调用可切换的
LLM provider 生成回答，并附**引用溯源**与**母婴健康免责**。是本项目的「智能」所在，与 MOD-session
直接配对（每轮取历史、写历史）。

---

## 一、数据流（answer 主流程）
```
session_ctx(user_msg, history, enterprise_id)
   │
   ├─1─ MOD-kb.search(query=user_msg, enterprise_id, top_k) ──▶ RetrievalHit[]  （横跨 **HQ 共享库 + B-end 企业库**，联合排序）
   ├─2─ (可选) 重排 cross-encoder: top_k ──▶ top_n
   ├─3─ 组装 prompt: system(企业定制) + 检索片段(带出处) + history + 问题 + 免责模板*
   ├─4─ LLMProvider.complete(messages)  ──▶ text
   ├─5─ 映射 citations ← RetrievalHit
   └─6─ Answer{text, citations, model, provider} ──▶ 写回 session history
```
> * 当且仅当问题/命中含母婴健康关键词时，尾部追加「非医疗诊断，严重时请就医」模板（可配置）。

---

## 二、企业定制 prompt
- `EnterprisePrompt` = 基础系统提示 + 企业产品结构/语气/政策 + 知识范围声明。
- **配置驱动**：每企业 `conf.yaml` 的 `prompt` 段（角色、产品类目、禁答范围、语气）。
- 检索片段注入时标注来源，并强约束「**仅依据所提供的资料作答，资料未覆盖则说不知道**」。
- 系统提示优先级高于用户/检索内容，防注入（见八）。

---

## 三、LLM provider 抽象（每企业可配置，C2）
- `LLMProvider` 接口：`complete(messages) -> str`（可扩展 streaming）。
- 实现：`OllamaProvider`（端侧本地，默认）/ `CloudAPIProvider`（云 API，可切换）。
- **配置**：`conf.yaml` 的 `llm: {provider, base_url, model, api_key?}`；运行时按企业解析。
- **切换零改动**：改配置即换 provider；`ProviderFactory` 按名实例化。
- **凭据安全**：api_key 仅运行时从配置/密钥注入，**绝不写会话存储或知识库**（与 MOD-session 一致）。
- **失败明确**：provider 不可达 → 返回明确错误，不静默降级到编造。

---

## 四、溯源与抗幻觉
- **citations**：每条回答附 `RetrievalHit{content, score, source, metadata}`，可映射回 kb 命中溯源。
- **低置信拒答**：检索为空或 top 分数 < 阈值 → 坦诚「知识库暂无相关内容」，绝不编造。
- **不暴露内部**：不向用户泄露系统提示 / 工具细节。
- **健康免责**：命中母婴健康类 → 强制免责模板，不替代医疗诊断。

---

## 五、对外契约 / 接口（自研）
- `Agent.for_enterprise(enterprise_id) -> EnterpriseAgent`：加载企业定制 prompt 与 provider。
- `EnterpriseAgent.answer(session_ctx, user_msg) -> Answer`：返回回答。
- `Answer` 结构：`{ text, citations: list[RetrievalHit], model, provider }`。
- `LLMProvider.complete(messages) -> str`：provider 抽象。
- `RetrievalHit` 结构：`{ content, score, source, metadata }`（来自 MOD-kb）。

---

## 六、实现步骤
1. **LLMProvider 抽象** + `OllamaProvider` / `CloudAPIProvider` + `ProviderFactory`。
2. **检索客户端**：封装 `MOD-kb.search`（带 `enterprise_id`）；可选 cross-encoder 重排。
3. **prompt 组装器**：企业定制 + 检索片段(带出处) + 历史 + 免责模板。
4. **answer() 主流程** + citations 映射。
5. **拒答/低置信分支** + 健康类免责注入。
6. **对接 MOD-session**：取历史、写历史（上下文由 session 历史窗口控制）。

---

## 七、关键风险与缓解
| 风险 | 缓解 |
|------|------|
| 幻觉 / 编造 | prompt 强约束 + 低置信拒答 + citations 强制 |
| 跨企业泄露 | 检索强制 `enterprise_id` 过滤（MOD-kb 保证，D4） |
| 数据出网 | provider 按企业配置；端侧企业用 Ollama 不出网（C2） |
| prompt 注入 | 检索片段标记为不可信上下文；系统提示优先级最高；拒绝执行改系统提示的指令 |
| 健康误导 | 强制免责模板，不替代医疗诊断 |
| provider 故障 | 明确错误，不静默编造 |
| 上下文膨胀 | 由 MOD-session 历史窗口控制，agent 只拿当前轮干净上下文 |

---

## 八、harness 验收草案（真实运行，非自述）
> 用 fake MOD-kb（预置已知片段）驱动，断言 RAG/溯源/拒答/免责/隔离。每个用例一个 `@agent` 脚本。

- `test_agent_rag.py`：kb 有答案时回答准确且带引用。
- `test_agent_provider_switch.py`：切 Ollama / Cloud 输出结构一致（`Answer` 形状不变）。
- `test_agent_no_hallucinate.py`：无相关答案时坦诚拒答，不编造。
- `test_agent_disclaimer.py`：健康类回答尾部带免责模板。
- `test_agent_citation_trace.py`：citations 可映射回 kb 具体命中。
- `test_agent_isolation.py`：不同企业检索不串（依赖 MOD-kb 隔离）。
- `test_agent_prompt_injection.py`：用户试图「忽略以上指令…」被拒，系统提示不被改。
- `test_agent_empty_kb.py`：kb 空时坦诚告知，不生成虚假建议。
- `test_providers.py`（@module agent，**已落地**）：生产闭环真实 provider 代码路径验证（本地 stub ollama / OpenAI 服务，不依赖真机）。
  - **P1 ollama 真实路径**：请求 `/api/chat` 体含 `model`/`messages`/`temperature` 且 `stream:false`（修复默认流式 NDJSON 致 `r.json()` 崩溃），正确解析 `message.content`。
  - **P2 cloud 真实路径**：请求带 `Bearer` 鉴权头，体含 `model`/`messages`/`temperature`/`max_tokens`，正确解析 `choices[0].message.content`。
  - **P3 grounding 透传**：pipeline 注入的【企业知识库】上下文确实进入真实 provider 收到的 messages（无截断）。

---

## 九、注意事项 / 雷区
- **禁止编造**：无依据时拒答，绝不生成看似合理的虚假母婴建议（高危红线）。
- 企业定制 prompt 不得覆盖安全/免责底线（prompt 注入防护）。
- 长上下文：历史由 MOD-session 管理，agent 只拿当前轮上下文，避免无限膨胀。
- provider 凭据运行时注入，绝不入知识库 / 会话存储。
- 本模块完全自研（方案 B），不 import Hermes。
