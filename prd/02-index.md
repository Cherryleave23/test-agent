# 模块索引（Index）— 开发时的地图

> Agent 开发一个模块前：先读 `01-architecture.md` 总览 → 本索引定位模块 → **只加载该模块的详解文件**。
> 不要一次性把 `modules/` 全部读进上下文（Principle P3：每模块独立可加载、一次一模块）。

| 模块 ID | 职责（一句话） | 详解文件 | Harness 标签 | 状态 |
|---------|----------------|----------|--------------|------|
| MOD-knowledge-ingest | 爬虫/OCR/多源统一适配接口，知识入库 | `modules/MOD-knowledge-ingest.md` | ingest | partial（P1：统一接口+注册表+真实爬虫+管线，OCR/PDF 待续） |
| MOD-kb | 企业定制知识库：分块/嵌入/向量检索 | `modules/MOD-kb.md` | kb | backlog |
| MOD-agent | RAG 问答核心：检索增强+企业定制 prompt+可切换 LLM | `modules/MOD-agent.md` | agent | backlog |
| MOD-session | 多员工会话隔离：(企业×员工×会话) 三级隔离 | `modules/MOD-session.md` | session | partial（P1：用户约束抽取累积 B + 短期摘要压缩 A，注入 prompt） |
| MOD-wechat | 微信(企业微信)接入：消息收发/身份识别/回调 | `modules/MOD-wechat.md` | wechat | backlog |
| MOD-deploy | 端侧 1家1实例部署：Docker/配置驱动/隔离 | `modules/MOD-deploy.md` | deploy | backlog |

## 加载纪律

- 进入某模块：加载 `01-architecture.md` + 该模块详解 **两份**上下文即可。
- 切到下一模块：丢弃上一份模块详解，加载下一份（一次一模块）。
- 改动波及多模块依赖时：回到 `01-architecture.md` 的依赖图核对，必要时更新本索引。

## 维护规则

- **新增模块**：在上方索引表加一行，新建 `modules/MOD-xxx.md`，更新 `01-architecture.md` 依赖图。
- **模块变更**：只改对应模块详解 + 索引/架构的相关行；**禁止**重写整个 PRD。
- **PRD 是单一事实源**：代码意图与 PRD 冲突时，先更新 PRD，再改代码。
- **验收只增不删**：每实现/修复一个行为，向 `harness/` 增加对应脚本；发现 bug 必加回归测试。
