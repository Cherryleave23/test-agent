# STATE.md — 工程状态快照

> 自动生成的项目状态快照，供会话交接 / 上下文压缩使用。
> 生成时间：2026-07-21 — Head `205f379` — 全量门禁 **9/9 ALL GREEN**（~50s）

## 1. 项目定位（不可变约束）

- **领域**：TOB 母婴垂类 agent。
- **部署形态**：可定制化的**端侧部署**到不同母婴企业；按其独特产品结构定制数据库。
- **知识转化**：配套功能包括爬虫、OCR、将异构知识归一为同一接口。
- **运行模型**：**1 家企业 = 1 个 agent**（端侧）；多员工通过微信在同一 agent 上工作。
- **会话隔离**：**单会话需独立**（用户级约束与记忆按 session 隔离）。

## 2. 技术栈与架构

- **语言**：Python 3.11（沙箱）；Node 22 备用。
- **门禁**：`scripts/run_harness.py` 发现带 `# @module <tag>` 的脚本，逐个子进程串行跑（默认 `--timeout 60`），要求 `RESULT: ALL GREEN`。
- **重型模型门控**：`RUN_REAL_MODEL=1` 才跑 bge 嵌入 + 重排器（~200s）；默认门禁跳过它们。
- **PRD 为唯一真相源**：新增/改动须配套 harness，red-run ≠ done，回归全绿才算完成。

### 目录结构（关键）
```
src/
  baby/        # MOD-baby-profile（客户+宝宝档案、消歧、归档）
  wechat/      # 网关：gateway.handle_message 注入消歧
  agent/       # pipeline.py：LLM 答案管线，注入 baby_block
  session/     # store.py：会话约束 + 焦点宝宝 + 消歧失败计数
  common/      # config.py：EnterpriseConfig（baby_profile_enabled, baby_db_path）
  ingest/      # P1 多源适配 + 爬虫 + 归一
prd/           # 模块 PRD（MOD-*.md）+ 02-index.md
harness/       # 各模块回归脚本
```

## 3. 模块实现进度

| 模块 | 状态 | 关键 commit | 门禁 |
|---|---|---|---|
| MOD-baby-profile | **完成（P1–P17 全绿）** | `a63d2aa`/`25055ea`/`4d154ef`/`46a8737` | 17/17 |
| MOD-ingest（P1） | 完成 | `3f5f106` | 8/8 |
| MOD-session 记忆 | 收敛为 UserConstraints | `64fe970` | — |
| MOD-kb 嵌入缓存 | 未启动（review 建议 D） | — | — |
| MOD-agent RAG 系统缓存 | 未启动（ROI 高于消歧缓存） | — | — |
| MOD-deploy 三项 P0 | 未启动（密钥环境变量化/出入站白名单/健康数据加密） | — | — |

## 4. 宝宝档案模块关键设计（已落地）

- **实体**：`Customer`(1→N) + `BabyProfile`（`upsert` 合并、`to_prompt_block`、`merge`）。
- **消歧**：`resolution.resolve_and_extract`（LLM 实体链接）+ `_rule_extract`（规则属性抽取）。
- **快速切换/P4**：`_has_baby_signal` 已扩展检查已知宝宝/客户名；`_match_known` 优先 `(customer,baby)` 精确匹配，歧义返回 None。
- **焦点稳定缓存（优化 B）**：`focus_is_stable(known, focus_baby_id, msg)` 为真 → 跳过 LLM，直接规则抽取归档到焦点宝宝（质量无损，因属性抽取本就规则化）。
- **数据一致性三项加固**：
  - pending 防污染：`find_baby_by_name` 仅匹配 `confirmed`；`prune_stale_pending(days)` 清理过期。
  - 消歧失败熔断：会话连续失败 ≥3（`session_resolution_fails`）→ 降级仅产品问答 + `logger.warning`。
  - 跨会话写锁：`_baby_locks` 按 `baby_id` 串行化 upsert/merge/delete（merge 按排序加锁防死锁）。
- **注入路径**：`wechat/gateway.handle_message` → `resolve_and_archive` → `pipeline.answer(baby_block=...)`。

## 5. 门禁与运行

```bash
# 默认（快速，~50s，9/9 绿）
python3.11 scripts/run_harness.py

# 含重型模型（~200s+，需显式开启）
RUN_REAL_MODEL=1 python3.11 scripts/run_harness.py
```

- **WAL 已开启**：`PRAGMA journal_mode=WAL`，非裸写。
- **缓存**：消歧已实施 **Prompt Caching（优化 C）**——`resolve_and_extract` 把稳定前缀（指令+known 清单）置首并开 `cache_control`；OpenAI 兼容端点靠自动前缀缓存，Anthropic 由 `_apply_cache_control` 显式加 ephemeral 断点；切换焦点不破坏缓存（焦点在断点后）。与优化 B（焦点稳定跳过 LLM）互补。

## 6. 待办与决策记录

| 项 | 结论 |
|---|---|
| 消歧 Prompt Caching（优化 C） | **已实施**（`46a8737`，P17 绿）：稳定前缀置首 + cache_control 断点 |
| MOD-agent RAG 答案系统 prompt 缓存 | 比消歧缓存 ROI 高，建议优先（未启动） |
| MOD-kb 嵌入缓存 | 中等收益，独立模块（未启动） |
| MOD-deploy 三项 P0 | 上线前必做，超出 baby 范围（未启动） |

## 7. 环境要点（避坑）

- **Git 鉴权**：`GITHUB_TOKEN`/`GH_TOKEN` 在 `~/.bashrc` + credential helper；非登录 shell 需手动 `GITHUB_TOKEN="$(...)" git push`。
- **commit 引用陷阱**：`git commit --amend` 改 hash 会令 README 死链；改用**独立 docs commit** 指向稳定父 hash。
- **对话级压缩不可行**：只能落盘快照（本文件）实现"持久化压缩"。

---
*本文件为状态快照，非 PRD。改动后以最新 HEAD + 门禁结果为准。*
