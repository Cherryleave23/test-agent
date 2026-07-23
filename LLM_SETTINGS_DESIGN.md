# Dataproc WebUI — LLM 设置页 框架设计 + 接口清单

> 依据 CVC 原则：P1 捕获意图 → P2 框架分析 → P3 索引化 PRD → P5 可执行验收。
> 状态：**✅ 已落地并通过验收**（2026-07-23）。harness `test_llm_settings.py` 8/8 ALL GREEN；OCR 回归 `test_dataproc_ocr.py` ALL GREEN；前端 `tsc --noEmit` 零错误。
> 目标场景：TOB 母婴，端侧 1 家 1 agent；此处只做"LLM 配置页"，OCR→LLM 抽取逻辑（范式②）是后续任务。

---

## P1 — 意图捕获（需求边界）

**目标**：在 dataproc WebUI 中提供一个**独立的 LLM 设置页**，让企业运维人员配置用于"OCR 文字 → 结构化字段补全"的 LLM provider，支持**本地 LMStudio（OpenAI 兼容，`http://localhost:1234/v1`）**与**云端 LLM（OpenAI 兼容端点）**，并复用 agent 的 WebUI 设置板块逻辑/结构。

### Must-have（必须满足）
| # | 需求 | 说明 |
|---|---|---|
| M1 | Provider 类型选择 | `none` / `lmstudio` / `cloud` / `ollama` 四选一 |
| M2 | base_url 可配 | lmstudio 预设 `http://localhost:1234/v1`；ollama 预设 `http://localhost:11434`；cloud 留空默认官方 |
| M3 | model 名称 | 必填（决定用哪个本地/云端模型） |
| M4 | api_key | 密码框，仅 cloud 需要；本地可空 |
| M5 | 连通性测试 | 真实请求，返回 成功/失败 + 延迟 + 可用模型列表 |
| M6 | 持久化 | 写入 `settings.json`（与 OCR 同存储），处理时注入引擎 |
| M7 | 凭据不落日志 | GET 返回时 `api_key` 一律脱敏为 `<set>`/`""` |

### Nice-to-have（后续可加，本次预留接口）
- N1：temperature / max_tokens 字段（沿用 agent `LLMConfig` 已有字段）
- N2：连通测试后回显 `/models` 列表供下拉选择
- N3：每仓库 LLM 覆盖（企业多产品线不同模型）

### Non-goals（本次不做）
- ❌ OCR→LLM 实际抽取管线（范式②的 `complete()` 调用、字段溯源 UI）
- ❌ 与 agent 运行时共享配置（dataproc 凭据与 agent 互不可见，强制隔离）
- ❌ 多 LLM 路由/熔断（agent 侧已有，dataproc 暂不引入）

---

## P2 — 框架分析（技术决策）

### 技术栈（沿用现有，零新增依赖）
- 前端：React + TypeScript（Vite），新增独立组件 `LLMSettingsPanel.tsx`
- 后端：FastAPI（现有 `gui/backend`），新增端点 + 一个最小 OpenAI 兼容客户端 `llm_client.py`
- 配置：dataproc `config.py` 的 `LLMConfig`（扩展 `kind` 域）

### 关键决策（写明理由）
1. **复用 agent 的"模式"而非"代码"**：agent 的 `providers-settings.tsx` / `LLMConfig` / `ProviderFactory` 逻辑清晰，但 dataproc 明确"零 `src.*` 依赖"（独立部署到企业端侧）。故在 dataproc 内**镜像**同样的字段结构与"连通测试"思路，不 import `src.*`。
2. **LMStudio / cloud 同为 OpenAI 兼容**：两者客户端逻辑完全一致（仅默认 base_url 与是否需 api_key 不同）。`ollama` 走自有 `/api/chat`。因此后端只需两类传输：`openai_compatible` 与 `ollama`。
3. **持久化复用 `settings.json`**：GUI 设置统一存 `repos._DEFAULT_BASE/settings.json`；处理时 `process.py` 把 LLM 配置注入 `DATAPROC_LLM_*` 环境变量，引擎 `load_config()` 已能读取——与现有 OCR 注入（`_apply_ocr_env`）完全一致。
4. **kind 域扩展**：`LLMConfig.kind` 从 `none|openai|ollama` 扩展为 `none|lmstudio|cloud|ollama`（`openai` 作为 `cloud` 别名保留兼容）。

### 模块分解（依赖关系）
```
前端
  LLMSettingsPanel.tsx ──> api.ts (getLLMSettings/updateLLMSettings/testLLM)
                              │
后端                          │
  main.py: GET/POST /settings/llm, POST /settings/llm/test
      │
      ├─> models.py: LLMSettings / SettingsUpdate(扩展 llm 字段)
      ├─> llm_client.py: OpenAICompatibleClient / OllamaClient (连通测试, 可注入 transport 便于测试)
      └─> process.py: _apply_gui_env() 扩展，注入 DATAPROC_LLM_*  (复用 _apply_ocr_env 模式)
              │
配置          │
  config.py: LLMConfig.kind 扩展 + as_dict() 脱敏
```

---

## P3 — 索引化 PRD（每模块具体接口）

> 每模块独立可读；改某模块只 reload 该节。

### M1 `config.py`（引擎配置）
```python
# LLMConfig.kind 域扩展
LLM_KINDS = ("none", "lmstudio", "cloud", "ollama")  # openai 视作 cloud 别名

@dataclass
class LLMConfig:
    kind: str = "none"          # none | lmstudio | cloud | ollama
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    temperature: float = 0.2    # N1 预留
    max_tokens: int = 1024      # N1 预留

    @property
    def enabled(self) -> bool:
        return self.kind not in ("none", "")
```
- `load_config()` 已读 `DATAPROC_LLM_*`，**无需改动**（kind 值新增即可被接受）。
- `as_dict()` 已对 `api_key` 脱敏为 `<set>`/`""`，**无需改动**。

### M2 `gui/backend/models.py`（请求体）
```python
class LLMSettings(BaseModel):
    kind: str = "none"                 # none|lmstudio|cloud|ollama
    base_url: str = ""
    model: str = ""
    api_key: str = ""                  # 写入时存明文；读出时后端脱敏
    temperature: float = 0.2           # N1
    max_tokens: int = 1024             # N1

class SettingsUpdate(BaseModel):       # 现有类扩展
    ocr_enabled: Optional[bool] = None
    run_real_ocr: Optional[bool] = None
    output_dir: Optional[str] = None
    repos_base: Optional[str] = None
    llm: Optional[LLMSettings] = None  # 新增：LLM 子块
```

### M3 `gui/backend/main.py`（端点）
```
GET  /settings/llm        -> { kind, base_url, model, api_key:"<set>"|"", temperature, max_tokens }
POST /settings/llm        -> 写入 settings.json 的 llm 块；返回脱敏后的当前值
POST /settings/llm/test   -> 连通测试（见 M4），返回结构化结果
```
- `GET /settings/llm`：从 `_load_gui_settings()` 取 `llm` 块，缺省回落 `DEFAULTS["llm"]`；`api_key` 脱敏。
- `POST /settings/llm`：合并入 `settings.json["llm"]`，`api_key` 原样持久化；返回脱敏值。
- 复用现有 `_load_gui_settings` / `_save_gui_settings`。

### M4 `gui/backend/llm_client.py`（最小客户端 + 连通测试）
```python
class LLMClientError(Exception): ...

def test_connection(cfg: dict) -> dict:
    """真实连通测试。transport 可注入（测试用）。
    返回: { ok: bool, latency_ms: int, models: list[str], endpoint: str, error: str|None }
    """
    kind = cfg["kind"]
    if kind in ("lmstudio", "cloud"):           # OpenAI 兼容
        base = (cfg["base_url"] or default_for(kind)).rstrip("/")
        # 1) GET {base}/models  (Authorization: Bearer api_key 若非空)
        # 2) 解析 models[].id
        # 3) 可选 POST {base}/chat/completions 微型 prompt 验证 completion
    elif kind == "ollama":
        base = (cfg["base_url"] or "http://localhost:11434").rstrip("/")
        # GET {base}/api/tags  -> models[].name
    else:  # none
        return {ok: False, error: "LLM 未启用 (kind=none)"}
```
- `default_for(kind)`：`lmstudio→http://localhost:1234/v1`，`ollama→http://localhost:11434`。
- **测试可注入 transport**：`test_connection(cfg, transport=stub)`，`stub.request(method,url,**kw)` 返回伪造响应，使 harness 在无网环境也能跑（P5）。

### M5 `gui/backend/process.py`（注入引擎）
```python
def _apply_gui_env():
    s = _load_gui_settings()
    # 现有 OCR 注入保持不变 ...
    # 新增 LLM 注入：
    llm = s.get("llm") or {}
    if llm.get("kind") and llm["kind"] != "none":
        os.environ["DATAPROC_LLM_KIND"] = llm["kind"]
        if llm.get("base_url"): os.environ["DATAPROC_LLM_BASE_URL"] = llm["base_url"]
        if llm.get("model"):    os.environ["DATAPROC_LLM_MODEL"] = llm["model"]
        if llm.get("api_key"):  os.environ["DATAPROC_LLM_API_KEY"] = llm["api_key"]
    else:
        os.environ.pop("DATAPROC_LLM_KIND", None)
```
（原 `_apply_ocr_env` 重命名为 `_apply_gui_env` 或直接在其后追加 LLM 注入；`_apply_ocr_env` 调用点同步更新。）

### M6 `frontend/src/api.ts`（客户端）
```ts
getLLMSettings:  () => req("GET", "/settings/llm"),
updateLLMSettings:(d:any)=> req("POST","/settings/llm", d),
testLLM:         (d:any)=> req("POST","/settings/llm/test", d),
```

### M7 `frontend/src/components/LLMSettingsPanel.tsx`（独立设置页）
- **复用 agent `providers-settings.tsx` 的结构**：provider 类型单选 → 条件字段 → 连通测试按钮 → 状态回显。
- Props：`{ onSaved?: (cfg: LLMSettings) => void }`
- 内部 state：`kind / base_url / model / api_key / testing / testResult`
- 行为：
  - 切 `kind` → 自动填充预设 `base_url`（lmstudio/ollama），`cloud` 清空等用户填。
  - `api_key` 用 `type="password"`。
  - 「测试连接」按钮 → `api.testLLM(current)` → 展示 `ok` / `latency_ms` / `models` 列表 / `error`。
  - 「保存」→ `api.updateLLMSettings(...)` → 回调通知。
- 字段与 agent `LLMConfig` 一一对应（kind/base_url/model/api_key/temperature/max_tokens），保证后续对接范式②零改字段。

### M8 挂载（独立页面）
- 在 `App.tsx` 增加顶部导航项「LLM 配置」，路由渲染 `<LLMSettingsPanel/>`（**单独页面**，符合"单独弄一个设置页面"），与现有「设置」下拉（OCR/输出目录）并列。
- 不强行塞进现有紧凑下拉，避免与 OCR 设置互相干扰。

### M9 样式
- 复用现有 `SettingsPanel` 的 CSS 变量（`settings-row` / `settings-input` / `settings-hint` / `settings-divider`），新增 `.llm-*` 局部类即可，风格统一。

---

## P5 — 验收设计（可执行 harness，必须 RUN 出 PASS/FAIL）

> 放在 `tools/dataproc/harness/` 或现有测试目录；CI 用 `pytest` / `python -m` 跑。

| 测试 | 验证点 | 判定 |
|---|---|---|
| `test_llm_settings_roundtrip` | POST /settings/llm 写入 → GET 读回，字段一致；api_key 脱敏 | PASS/FAIL |
| `test_llm_kind_validation` | kind=none 时 GET 返回 enabled=false；非法 kind 被拒（422） | PASS/FAIL |
| `test_apply_gui_env_injects` | 写 settings.json llm 块 → 调 `_apply_gui_env()` → `os.environ["DATAPROC_LLM_KIND"]` 等于写入值 | PASS/FAIL |
| `test_connection_mock_openai` | 注入 stub transport 模拟 200 + models 列表 → `test_connection` 返回 ok=True, models 非空, latency>0 | PASS/FAIL |
| `test_connection_mock_ollama` | stub 模拟 `/api/tags` → ok=True | PASS/FAIL |
| `test_connection_failure` | stub 抛网络错 → ok=False, error 非空, 不崩溃 | PASS/FAIL |
| `test_connection_none` | kind=none → ok=False, error 含 "未启用" | PASS/FAIL |
| `test_preset_baseurl` | kind=lmstudio → 默认 base_url=`http://localhost:1234/v1`；ollama→`http://localhost:11434` | PASS/FAIL |

**硬性闸门**：上述任一 FAIL ⇒ 模块未完成；全绿 ⇒ 可进入编码下一阶段（范式②抽取对接）。

### ✅ 实际验收结果（2026-07-23）
- `harness/test_llm_settings.py`：**8/8 ALL GREEN**（L1–L8）
- `harness/test_dataproc_ocr.py`：回归 **ALL GREEN**（I16 默认绿 + 真实 OCR 门控 SKIP）
- 前端 `npx tsc --noEmit`：**零错误**
- 引擎侧 `load_config()` 消费 GUI 注入的 `DATAPROC_LLM_*` 端到端验证通过；`openai` 别名归一化、`api_key` 脱敏、非法 kind 回落 `none` 均正确。

---

## 与 agent 的复用对照表
| agent 侧（参考源） | dataproc 侧（本次实现） | 复用方式 |
|---|---|---|
| `common.config.LLMConfig(kind,base_url,model,api_key,temperature,max_tokens)` | `config.py:LLMConfig` 扩展 kind 域 + 同名字段 | 字段镜像 |
| `providers.py:ProviderFactory(mock/ollama/cloud)` | `llm_client.py:test_connection`（openai_compatible/ollama） | 结构镜像（不 import） |
| `providers-settings.tsx`（类型单选→字段→测试） | `LLMSettingsPanel.tsx` | UI 范式复用 |
| 连通测试思路（真实请求+模型列表） | `POST /settings/llm/test` | 思路复用 |
