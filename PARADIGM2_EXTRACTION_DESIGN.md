# 范式② 抽取层落地设计（OCR 锚定 + LLM 补全 + 冲突标记）

> 现状盘点：范式②骨架**已存在**——`dataproc/llms/__init__.py`（`ToolLLMProvider` / `OpenAICompatProvider` / `OllamaProvider` / `MockProvider` + `from_config`）、`structurer.py`（`structure(text, provider)` 已调 `provider.complete` 并合并）、`build.py` 已接线 `provider = from_config(cfg.llm)`。
> 本次只补**两块真实缺口**，不重复造轮子。
> 状态：**✅ landed**（设计→编码→harness 真实验收，全绿，已纳入全量回归）。

## P1 — 意图（本次范围）
| # | 缺口 | 性质 |
|---|---|---|
| G1 | `from_config` 不认 `lmstudio` kind（设置页已暴露该选项）→ build 时 `ValueError` 崩溃 | **回归修复（必须）** |
| G2 | `_merge` 让 LLM 覆盖规则全部字段，无"OCR 为事实源 / 冲突 needs_review"防幻觉逻辑 | **合规增强（核心）** |

Non-goals：换 provider 架构；动 `schema.py`；真实网络调用测试（harness 用 MockProvider + stub）。

## P2 — 框架（复用现有）
- `llms/__init__.py`：`from_config` 增加 `lmstudio` 分支（OpenAI 兼容，默认 base_url=`http://localhost:1234/v1`，复用 `LLM_DEFAULT_BASE_URL`）。
- `structurer.py`：用 `_fuse(rule, llm)` 替换 `_merge`，实现 OCR 锚定融合 + 冲突检测；`StructuringResult` 增 `needs_review: bool`。
- `build.py`：`st.needs_review` → 产品 `status="needs_review"`；manifest `counts` 增 `needs_review`。

## P3 — 接口与融合规则
### 字段权威分级（`structurer.py`）
```python
_OCR_AUTH_FIELDS = ("reg_number", "net_content", "stage")  # 数字/编码：OCR 为准
# 其余 brand/name/manufacturer/age_range：描述字段，LLM 优先补全
```
### `_fuse(rule, llm) -> (fields, conflicts)`
- 权威字段：取 `rule` 值（OCR 为准）；`rule` 空才用 `llm`；双方非空且不同 → 记冲突，保留 `rule`。
- 描述字段：取 `llm` 值（优先补全）；`llm` 空才用 `rule`；双方非空且不同 → 记冲突，保留 `rule`（保守、不编造）。
- 返回 `(fields, conflicts)`；`conflicts` 非空 → `needs_review=True`。

### `from_config` 新增分支
```python
if kind == "lmstudio":
    base = cfg.base_url or LLM_DEFAULT_BASE_URL["lmstudio"]
    return OpenAICompatProvider(base, cfg.model, cfg.api_key)
```

## P5 — 验收（harness，必须 RUN PASS/FAIL）
`harness/test_paradigm2.py`：
| 测试 | 验证点 | 判定 |
|---|---|---|
| P2a | `from_config(LLMConfig(kind="lmstudio"))` → `OpenAICompatProvider`，base=`http://localhost:1234/v1` | PASS/FAIL |
| P2b | `from_config` none→None；mock→MockProvider；cloud/openai→OpenAICompat；ollama→OllamaProvider（回归） | PASS/FAIL |
| P2c | `structure(text, MockProvider(json))` 描述字段被 LLM 补全（brand 空→填） | PASS/FAIL |
| P2d | 权威字段冲突：rule.reg_number="国食注字A"，llm.reg_number="B" → 保留 A，`needs_review=True` | PASS/FAIL |
| P2e | 权威字段一致：rule.net_content="800g"==llm → 无冲突 | PASS/FAIL |
| P2f | 描述字段冲突：rule.brand="a2"，llm.brand="a2 Platinum" → 保留 rule，`needs_review=True` | PASS/FAIL |
| P2g | `provider=None` → 纯规则兜底（rule-only），`needs_review=False`（回归） | PASS/FAIL |
| P2h | `structure` LLM 返回非 JSON → `parse_failed=True`，不崩（回归） | PASS/FAIL |
| P2i | `build_bundle` 在 mock provider + 图片内容下，冲突产品 `status="needs_review"`，manifest 计数含 `needs_review` | PASS/FAIL |

**闸门**：任一 FAIL ⇒ 未完成；全绿 ⇒ 范式②抽取层可用（配合 GUI 已配置的 LMStudio/云端 LLM）。

## 验收结果（2026-07-23）
- `python3 harness/test_paradigm2.py` → **RESULT: ALL GREEN**（P2a~P2i 全 9 项 PASS）。
- 全量回归：`for h in harness/test_*.py` 共 **51 个 harness 全部 ALL GREEN，零回归**
  （含 `test_llm_settings.py`、`test_dataproc_ocr.py`、`test_dataproc_pdf.py`、`test_dataproc_resolver.py`）。
- 落地变更：
  1. `dataproc/llms/__init__.py`：`from_config` 增加 `lmstudio` 分支（G1 回归修复）。
  2. `dataproc/structurer.py`：`_merge` → `_fuse`（OCR 锚定融合 + 冲突检测），`StructuringResult.needs_review`；
     注册号正则 `国食注字\S+` → `国食注字\s*\S+`（兼容真实标签「国食注字 YPxxxx」空格）。
  3. `dataproc/build.py`：`st.needs_review` → `status="needs_review"`；manifest `counts.needs_review`。
  4. `dataproc/repo.py`（新增）：引擎侧 `init_repo(repo_dir, name, namespace, output_dir)`，写入与 GUI 同契约的 `repo.json` + 三大总文件夹，
     供引擎脚本/验收 harness 零 GUI 依赖调用。
- 融合语义（最终）：权威字段（reg_number/net_content/stage）以规则为准；描述字段（brand/name/manufacturer/age_range）LLM 优先补全；
  **任意字段双非空且取值不同 → 保守保留规则(OCR)锚定值并标记 `needs_review`**，绝不静默采用任一方。
