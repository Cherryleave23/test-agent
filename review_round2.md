# 代码审查报告（第二轮）

**项目**: 母婴垂类 ToB RAG Agent  
**审查范围**: 数据转化模块 + Admin 后台模块（共 12 个文件）  
**审查日期**: 2026-07-22  
**审查标准**: P0(安全) / P1(功能缺陷) / P2(代码质量) / P3(建议优化)  

---

## 目录

1. [审查总览](#1-审查总览)
2. [逐文件审查](#2-逐文件审查)
   - 2.1 [src/admin/server.py](#21-srcadminserverpy)
   - 2.2 [src/admin/models.py](#22-srcadminmodelspy)
   - 2.3 [src/admin/pages.py](#23-srcadminpagespy)
   - 2.4 [tools/dataproc/adapters/\_ppstructure.py](#24-toolsdataprocadapters_ppstructurepy)
   - 2.5 [tools/dataproc/adapters/pdf.py](#25-toolsdataprocadapterspdfpy)
   - 2.6 [tools/dataproc/adapters/image_table.py](#26-toolsdataprocadaptersimage_tablepy)
   - 2.7 [tools/dataproc/classifier.py](#27-toolsdataprocclassifierpy)
   - 2.8 [harness/test_admin_api.py](#28-harnesstest_admin_apipy)
   - 2.9 [harness/test_ppstructure_table.py](#29-harnesstest_ppstructure_tablepy)
   - 2.10 [harness/test_dataproc_pdf.py](#210-harnesstest_dataproc_pdfpy)
   - 2.11 [src/kb/store.py](#211-srckbstorepy)
   - 2.12 [tools/dataproc/build.py](#212-toolsdataprocbuildpy)
3. [问题汇总](#3-问题汇总)
4. [总体评分与总结](#4-总体评分与总结)

---

## 1. 审查总览

| 级别 | 数量 | 说明 |
|------|------|------|
| P0 | 4 | 安全漏洞：跨租户数据越权、XSS、连接泄露导致资源耗尽 |
| P1 | 12 | 功能缺陷：时序攻击、线程安全、资源泄露、文件句柄泄露等 |
| P2 | 18 | 代码质量：重复代码、缺少日志、性能问题等 |
| P3 | 12 | 建议优化：最佳实践、可维护性改进 |

**总体评分: 3.0 / 5.0**

项目整体架构清晰，安全设计意识较好（Bearer Token 认证、表名白名单、HTML 转义、敏感字段过滤等），但存在多个跨租户越权问题和资源泄露问题需要在上线前修复。

---

## 2. 逐文件审查

### 2.1 src/admin/server.py

**文件定位**: FastAPI 管理后台服务，5 大管理板块（LLM/数据库/门店/网关/宝宝档案）  
**代码行数**: 385 行  
**审查结论**: 存在 2 个 P0 跨租户越权问题和多个 P1 问题

---

#### [P0-01] 跨租户数据越权 — list_employees / list_gateway_bindings / list_babies

**位置**: 第 237 行、第 272 行、第 316 行

```python
# 第 237 行
def list_employees(enterprise_id: Optional[str] = None):
    ent = enterprise_id or cfg.enterprise_id  # ← 允许传入任意 enterprise_id

# 第 272 行
def list_gateway_bindings(enterprise_id: Optional[str] = None):
    ent = enterprise_id or cfg.enterprise_id  # ← 同上

# 第 316 行
def list_babies(enterprise_id: Optional[str] = None, employee_id: Optional[str] = None):
    ent = enterprise_id or cfg.enterprise_id  # ← 同上
```

**问题**: 任意已认证的 admin 用户可通过传入 `enterprise_id` 参数查询**其他企业**的员工列表、微信网关绑定（含脱敏 token）、宝宝档案。虽然 bot_token 做了脱敏处理，但员工姓名、微信名、宝宝档案概览等隐私数据被泄露。对于 ToB 多租户系统，这是严重的越权访问漏洞。

**修复建议**: 移除 `enterprise_id` 查询参数，强制使用 `cfg.enterprise_id`；或增加超级管理员角色判断，仅超级管理员可跨企业查询。

```python
@app.get("/api/employees", dependencies=[Depends(_verify_token)])
def list_employees():
    ent = cfg.enterprise_id  # 强制使用当前实例的企业 ID
    # ...
```

---

#### [P0-02] 跨租户数据修改 — confirm_product / delete_product 无 enterprise_id 校验

**位置**: 第 196-214 行（server.py）+ 第 206-237 行（store.py）

```python
# server.py 第 196-204 行
def db_confirm(product_id: int, value: str, table: str = "products_milk"):
    validate_table(table)
    store = _get_store()
    store.confirm_product(product_id, value, table)  # ← 不传 enterprise_id

# store.py 第 206-217 行
def confirm_product(self, product_id: int, value: str, table: str = "products_milk") -> None:
    col = self._PENDING_COL[table]
    with connect(self.db_path) as conn:
        conn.execute(
            f"UPDATE {table} SET {col}=? WHERE id=?",
            (value, product_id),  # ← 无 enterprise_id 过滤
        )
```

**问题**: `confirm_product` 和 `delete_product` 均不校验 `enterprise_id`。已认证的 admin 可通过枚举 `product_id` 确认或删除**任意企业**的商品数据。`delete_product` 还会连带删除关联的 corpus 和 Chroma 向量，造成不可逆的数据破坏。

**修复建议**: 在 SQL 中加入 `enterprise_id` 过滤条件。

```python
# store.py
def confirm_product(self, product_id: int, value: str, table: str = "products_milk",
                    enterprise_id: str = "") -> None:
    col = self._PENDING_COL[table]
    with connect(self.db_path) as conn:
        conn.execute(
            f"UPDATE {table} SET {col}=? WHERE id=? AND enterprise_id=?",
            (value, product_id, enterprise_id),
        )
```

---

#### [P1-01] Token 比较使用非常量时间比较（时序攻击）

**位置**: 第 69 行

```python
if credentials is None or credentials.credentials != expected:
```

**问题**: 使用 `!=` 进行 token 比较存在时序攻击风险。Python 字符串比较在第一个不匹配字符处短路返回，攻击者可通过测量响应时间逐字符推断 token。第 26 行已导入 `secrets` 模块但未使用。

**修复建议**:

```python
import secrets

def _verify_token(credentials: HTTPAuthorizationCredentials = Security(_security)):
    expected = _get_admin_token()
    if not expected:
        return
    if credentials is None or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(401, "未授权：请提供有效的 Bearer Token")
```

---

#### [P1-02] SQLite 连接泄露 — `with connect() as conn` 不关闭连接

**位置**: 第 168、219、227、239、254、265、274、289、306 行（所有 `with connect(...) as conn:` 调用）

```python
with connect(cfg.db_path) as conn:
    corpus_count = conn.execute("SELECT COUNT(*) FROM corpus").fetchone()[0]
```

**问题**: `sqlite3.Connection` 的 `__enter__`/`__exit__` 仅管理事务（commit/rollback），**不会关闭连接**。每次 API 调用都打开一个新连接但不关闭，长时间运行的服务会累积大量未关闭连接，最终导致资源耗尽（文件描述符耗尽、SQLite 锁争用）。

`common/db.py` 中已有正确的 `db_tx` 上下文管理器（会 `conn.close()`），但代码未使用。

**修复建议**: 使用 `db_tx` 替代 `with connect() as conn:`，或在 finally 中显式关闭。

```python
from common.db import db_tx

with db_tx(cfg.db_path) as conn:
    corpus_count = conn.execute("SELECT COUNT(*) FROM corpus").fetchone()[0]
```

---

#### [P1-03] `_get_store()` 线程不安全的懒初始化

**位置**: 第 91-101 行

```python
_store_holder: dict = {"store": None}

def _get_store() -> KnowledgeStore:
    if _store_holder["store"] is None:
        _store_holder["store"] = KnowledgeStore(...)
    return _store_holder["store"]
```

**问题**: FastAPI 在多线程环境运行（uvicorn 线程池），多个请求可能同时进入 `is None` 分支，创建多个 `KnowledgeStore` 实例。多个 `chromadb.PersistentClient` 指向同一目录会导致数据库锁冲突甚至损坏。

**修复建议**: 使用 `threading.Lock` 保护初始化，或在 `create_app` 时提前初始化。

```python
import threading
_store_lock = threading.Lock()

def _get_store() -> KnowledgeStore:
    if _store_holder["store"] is None:
        with _store_lock:
            if _store_holder["store"] is None:  # double-check
                _store_holder["store"] = KnowledgeStore(...)
    return _store_holder["store"]
```

---

#### [P1-04] YAML 路径验证与文档声明不符（路径遍历风险）

**位置**: 第 74-82 行

```python
def _validate_yaml_path(yaml_path: str) -> str:
    """验证 yaml 路径合法（防路径遍历，允许 .yaml/.yml 后缀的任意路径）。"""
    abs_path = os.path.abspath(yaml_path)
    if not abs_path.endswith((".yaml", ".yml")):
        raise HTTPException(403, ...)
    return abs_path
```

**问题**: 文件头部文档注释（第 17 行）声称"YAML 路径验证：仅允许写入 deploy/ 目录下的配置文件"，但实际实现仅检查后缀，**不限制目录**。若 `AGENT_CONFIG_PATH` 环境变量被设置为 `/etc/cron.d/evil.yaml` 等路径，`POST /api/llm` 会向该路径写入数据。虽然路径来源是环境变量（非直接用户输入），但这违反了最小权限原则，且文档声明具有误导性。

**修复建议**: 增加目录限制，或修正文档声明。

```python
def _validate_yaml_path(yaml_path: str) -> str:
    abs_path = os.path.abspath(yaml_path)
    if not abs_path.endswith((".yaml", ".yml")):
        raise HTTPException(403, f"配置文件必须是 .yaml 或 .yml 后缀: {abs_path}")
    # 限制仅在 deploy/ 目录下（或配置允许的目录）
    allowed_dir = os.path.abspath(os.path.dirname(abs_path))
    deploy_dir = os.path.abspath("deploy")
    if not allowed_dir.startswith(deploy_dir + os.sep) and allowed_dir != deploy_dir:
        raise HTTPException(403, f"配置文件必须在 deploy/ 目录下: {abs_path}")
    return abs_path
```

---

#### [P1-05] API Key 明文写入 YAML 文件

**位置**: 第 157 行

```python
data["llm"]["api_key"] = update.api_key or None
```

**问题**: 用户通过 API 提交的 LLM API Key 被明文写入 YAML 配置文件。若该文件权限不当或被版本控制，API Key 将泄露。此外，当用户提交空 `api_key` 时，已有 key 被覆盖为 `None`，造成配置丢失。

**修复建议**: 
1. API Key 应存储在环境变量或加密的 secret store 中，而非明文 YAML。
2. 空提交时不覆盖已有 key：`if update.api_key: data["llm"]["api_key"] = update.api_key`。

---

#### [P2-01] `secrets` 模块导入但未使用

**位置**: 第 26 行

```python
import secrets  # 从未使用
```

**修复建议**: 删除未使用的导入，或用于 P1-01 的 token 比较。

---

#### [P2-02] 缺少安全事件日志

**位置**: 全文

**问题**: 无任何日志记录：认证失败、配置修改、商品删除等安全敏感操作均无日志。审计追踪无法实现。

**修复建议**: 添加 `logging` 模块，记录关键操作。

---

#### [P3-01] `_get_admin_token()` 每次请求读取环境变量

**位置**: 第 58-61 行

**问题**: 每次请求都调用 `os.environ.get()`，虽然性能影响极小，但设计上应在启动时读取一次。

---

### 2.2 src/admin/models.py

**文件定位**: Admin 模块数据模型与 DB schema  
**代码行数**: 79 行  
**审查结论**: 整体简洁，存在 P2 级脱敏不足问题

---

#### [P2-03] mask_token 脱敏力度不足

**位置**: 第 75-79 行

```python
def mask_token(token: str) -> str:
    """脱敏 bot_token：保留前 8 字符 + 省略号。"""
    if not token:
        return ""
    return token[:8] + "…"
```

**问题**: 保留前 8 个字符对于 bot token 来说暴露过多。iLink Bot Token 通常有固定前缀格式，前 8 个字符可能足以缩小暴力破解范围。

**修复建议**: 仅保留前 4 个字符 + `****` + 后 4 个字符，或仅显示 `****` + 后 4 位。

```python
def mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "****"
    return token[:4] + "****" + token[-4:]
```

---

#### [P2-04] Pydantic 模型缺少输入校验

**位置**: 第 40-65 行

```python
class StoreCreate(BaseModel):
    enterprise_id: str       # 无长度限制、无格式校验
    enterprise_name: str     # 同上
    db_path: str = "instance.db"  # 用户可控的文件路径
```

**问题**: 所有字段均无长度限制、格式校验或白名单约束。特别是 `db_path` 字段接受任意字符串，可被注入恶意路径（如 `../../etc/malicious.db`），后续使用该路径创建 SQLite 文件时可覆盖系统文件。

**修复建议**: 使用 Pydantic 的 `Field` 和 `validator` 添加约束。

```python
from pydantic import Field, validator

class StoreCreate(BaseModel):
    enterprise_id: str = Field(..., min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')
    enterprise_name: str = Field(..., min_length=1, max_length=128)
    db_path: str = Field("instance.db", max_length=256)

    @validator('db_path')
    def validate_db_path(cls, v):
        if '..' in v or v.startswith('/'):
            raise ValueError('db_path 不允许包含 .. 或以 / 开头')
        return v
```

---

#### [P3-02] `db_path` 默认值为相对路径

**位置**: 第 52 行

```python
db_path: str = "instance.db"
```

**问题**: 相对路径依赖工作目录，可能导致文件创建在意外位置。

**修复建议**: 使用绝对路径或明确说明相对路径基准。

---

### 2.3 src/admin/pages.py

**文件定位**: HTML 页面渲染（XSS 防护）  
**代码行数**: 379 行  
**审查结论**: 存在 P0 级 XSS 和 P1 级 XSS 防护不一致问题

---

#### [P0-03] XSS — innerHTML 直接插入 API 响应

**位置**: 第 141 行

```javascript
document.getElementById('result').innerHTML = '<div class="card" style="background:#d1fae5;">' + data.message + '</div>';
```

**问题**: `data.message` 来自 API 响应，直接插入 `innerHTML` 未经转义。当前 `update_llm_config` 返回的 message 是硬编码字符串（安全），但：
1. 若 API 返回错误，FastAPI 默认返回 `{"detail": "..."}`，此时 `data.message` 为 `undefined`（低风险）。
2. 若后续迭代在 message 中拼接用户可控数据（如 `yaml_path`），则直接导致存储型 XSS。
3. 这违反了文件头部"所有动态值经 html.escape() 转义，防 XSS"的设计原则。

**修复建议**: 使用 `textContent` 或对 `data.message` 进行 HTML 转义。

```javascript
const resultDiv = document.getElementById('result');
resultDiv.innerHTML = '';
const div = document.createElement('div');
div.className = 'card';
div.style.background = '#d1fae5';
div.textContent = data.message || '';
resultDiv.appendChild(div);
```

---

#### [P0-04] XSS — 待确认商品列表中商品名称转义不完整 + table 参数注入

**位置**: 第 192-197 行

```javascript
const safeName = (p.name || '').replace(/</g, '&lt;');
const safeBrand = (p.brand || '').replace(/</g, '&lt;');
html += `<tr><td>${p.id}</td><td>${safeName}</td><td>${safeBrand}</td><td>${p.table}</td>
  <td><input id="val-${p.id}" placeholder="注册号/批准文号" style="width:160px;">
  <button onclick="confirmProduct(${p.id},'${p.table}')" class="btn">确认</button>
  <button onclick="deleteProduct(${p.id},'${p.table}')" class="btn btn-danger">删除</button></td></tr>`;
```

**问题**:
1. `safeName`/`safeBrand` 仅转义 `<`，未转义 `&`。虽然 `<` 转义已可阻止标签注入，但不转义 `&` 可导致 HTML 实体解析异常。商品名称来自 OCR/用户上传的 markdown，可被攻击者控制。
2. `p.table` 被直接插入 HTML 内容（`${p.table}`）和 `onclick` 属性（`'${p.table}'`）。当前 `p.table` 来源于硬编码的表名（安全），但代码模式危险——若后续扩展允许动态表名，单引号注入将导致 XSS。

**修复建议**: 使用统一的 `esc()` 函数（已在其他页面定义）进行完整转义。

```javascript
const safeName = esc(p.name);
const safeBrand = esc(p.brand);
const safeTable = esc(p.table);
html += `<tr><td>${p.id}</td><td>${safeName}</td><td>${safeBrand}</td><td>${safeTable}</td>
  <td><input id="val-${p.id}" placeholder="注册号/批准文号" style="width:160px;">
  <button onclick="confirmProduct(${p.id},'${safeTable}')" class="btn">确认</button>
  <button onclick="deleteProduct(${p.id},'${safeTable}')" class="btn btn-danger">删除</button></td></tr>`;
```

---

#### [P1-06] 服务端与客户端 XSS 转义策略不一致

**位置**: 第 44-46 行 vs 第 261/317/354 行

**问题**: 
- 服务端使用 `html.escape()`（转义 `&`, `<`, `>`, `"`, `'`）
- 客户端 JavaScript 的 `esc()` 函数仅转义 `&`, `<`, `>`（不转义 `"` 和 `'`）
- database 页面中 `safeName`/`safeBrand` 仅转义 `<`（最弱）

三处转义策略不一致，增加了遗漏风险。

**修复建议**: 统一使用完整的 HTML 转义函数，在 JavaScript 中也转义 `"` 和 `'`。

```javascript
function esc(s) {
    return String(s || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
```

---

#### [P2-05] JavaScript `esc()` 函数在三个页面中重复定义

**位置**: 第 261 行、第 317 行、第 354 行

**问题**: 完全相同的 `esc()` 函数在 `render_stores_page`、`render_gateway_page`、`render_babies_page` 中重复定义。

**修复建议**: 提取为公共 JavaScript 片段，在 `_BASE_CSS` 或单独的 `<script>` 块中定义一次。

---

#### [P2-06] database 页面 loadStatus 中 innerHTML 使用模板字符串拼接

**位置**: 第 176-184 行

```javascript
document.getElementById('status').innerHTML = `
    <div class="stat"><div class="stat-num">${d.corpus_count}</div>...
`;
```

**问题**: 虽然数值字段安全，但 `d.inbox_exists ? '✓' : '✗'` 中的 `inbox_exists` 如果被篡改为非布尔值，可能产生意外行为。整体模式不够健壮。

---

#### [P3-03] CSS 嵌入 Python 字符串，维护困难

**位置**: 第 10-41 行

**问题**: 190 行 CSS 嵌入在 Python 字符串中，无法使用 IDE 的 CSS 语法检查、自动补全等功能。

**修复建议**: 考虑将 CSS 提取为独立文件或使用模板引擎。

---

### 2.4 tools/dataproc/adapters/_ppstructure.py

**文件定位**: PP-Structure 表格识别共享模块  
**代码行数**: 125 行  
**审查结论**: 设计清晰，存在 P2 级线程安全和功能局限问题

---

#### [P2-07] 全局可变状态的线程安全问题

**位置**: 第 51-52 行

```python
_pp_engine: Optional[object] = None
_pp_initialized: bool = False
```

**问题**: `get_ppstructure()` 中的单例初始化模式非线程安全。多线程同时调用时可能重复初始化引擎。

**修复建议**: 使用 `threading.Lock` 保护，或使用 `functools.lru_cache`。

---

#### [P2-08] TableHTMLParser 不处理 colspan/rowspan

**位置**: 第 20-47 行

**问题**: PP-Structure 输出的 HTML 表格可能包含 `colspan` 和 `rowspan` 属性。当前解析器忽略这些属性，导致二维 cells 数组与实际表格结构不匹配，可能产生错位。

**修复建议**: 在 `handle_starttag` 中解析 `colspan`/`rowspan` 属性，并在构建二维数组时进行填充。

---

#### [P3-04] `extract_tables` 参数缺少类型注解

**位置**: 第 80 行

```python
def extract_tables(img_array) -> list:
```

**问题**: `img_array` 参数无类型注解，期望为 `np.ndarray` 但未标注。

**修复建议**: `def extract_tables(img_array: "np.ndarray") -> list:`

---

#### [P3-05] `reset()` 函数缺少文档说明使用场景

**位置**: 第 121-125 行

**问题**: 仅注释"测试用"，但未说明为何测试需要重置、是否有生产场景需要调用。

---

### 2.5 tools/dataproc/adapters/pdf.py

**文件定位**: PDF 适配器（数字直抽 + 扫描件 OCR）  
**代码行数**: 99 行  
**审查结论**: 存在 P1 级资源泄露和 P2 级性能问题

---

#### [P1-07] `_render_pages` 不关闭 fitz 文档对象

**位置**: 第 24-31 行

```python
def _render_pages(path: str):
    import fitz
    from PIL import Image
    doc = fitz.open(path)
    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        yield Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    # ← doc 从未关闭
```

**问题**: `fitz.open()` 返回的文档对象持有文件句柄和内存资源，函数结束后从未调用 `doc.close()`。在批量处理 PDF 时会导致文件句柄耗尽。

**修复建议**: 使用 `try/finally` 或上下文管理器。

```python
def _render_pages(path: str):
    import fitz
    from PIL import Image
    doc = fitz.open(path)
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            yield Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()
```

---

#### [P1-08] `_ocr_images` 中异常处理不完整

**位置**: 第 49-58 行

```python
for line in res[0] or []:
    if not line:
        continue
    box, (txt, score) = line  # ← 解包可能失败
```

**问题**: PaddleOCR 返回结果格式可能因版本不同而变化。`box, (txt, score) = line` 解包假设固定格式，若格式不符会抛 `ValueError`，导致整个 OCR 中断。

**修复建议**: 增加防御性解包。

```python
for line in res[0] or []:
    if not line or len(line) < 2:
        continue
    try:
        box, (txt, score) = line
    except (ValueError, TypeError):
        continue
```

---

#### [P2-09] PaddleOCR 实例每次调用都重新创建

**位置**: 第 43 行

```python
ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
```

**问题**: PaddleOCR 初始化开销大（加载模型），每次调用 `_ocr_images` 都创建新实例。PP-Structure 已使用单例模式，但 PaddleOCR 没有。

**修复建议**: 仿照 `_ppstructure.py` 的单例模式，缓存 PaddleOCR 实例。

---

#### [P2-10] `_digital_text` 和 `_page_count` 静默吞掉所有异常

**位置**: 第 20-21 行、第 94-99 行

```python
except Exception:
    return ""
```

**问题**: 所有异常被静默吞掉，包括文件不存在、权限错误、PDF 损坏等。无日志输出，调试困难。

**修复建议**: 至少记录 warning 级别日志。

```python
except Exception as e:
    logger.warning("pypdf 文本抽取失败: %s: %s", type(e).__name__, e)
    return ""
```

---

#### [P2-11] `extract` 方法无文件存在性检查

**位置**: 第 76-91 行

**问题**: `extract` 方法直接调用 `_digital_text(path)` 和 `_render_pages(path)`，不检查文件是否存在。若文件不存在，`_digital_text` 返回空串（被异常吞掉），然后 `_render_pages` 会抛出异常。

**修复建议**: 在入口处检查 `os.path.isfile(path)`。

---

### 2.6 tools/dataproc/adapters/image_table.py

**文件定位**: 图片/规格表/电商长图适配器  
**代码行数**: 112 行  
**审查结论**: 存在 P1 级资源泄露和 P2 级切片重复问题

---

#### [P1-09] PIL Image 文件句柄未关闭

**位置**: 第 95 行

```python
pil = Image.open(path).convert("RGB")
```

**问题**: `Image.open()` 打开的文件句柄在 `convert("RGB")` 后可能被保留（PIL 的 lazy loading 机制）。虽然 `convert` 会触发加载，但底层文件句柄不一定被关闭。

**修复建议**: 使用上下文管理器。

```python
from PIL import Image
with Image.open(path) as pil:
    arr = np.array(pil.convert("RGB"))
```

---

#### [P2-12] `_slice_long` 末尾切片可能与循环最后一片重复

**位置**: 第 39-49 行

```python
step = SLICE_H - SLICE_OVERLAP  # 1080
for y in range(0, max(h - SLICE_H, 0) + 1, step):
    yield gray[y:y + SLICE_H]
if h - SLICE_H > 0:
    yield gray[h - SLICE_H:h]  # 末片收尾
```

**问题**: 当 `(h - SLICE_H) % step == 0` 时，循环的最后一次迭代 `y = h - SLICE_H`，产生的切片与"末片收尾"完全相同，导致同一区域被 OCR 两次。

**修复建议**: 记录最后一片的 y 值，避免重复。

```python
last_y = 0
for y in range(0, max(h - SLICE_H, 0) + 1, step):
    yield gray[y:y + SLICE_H]
    last_y = y
if h - SLICE_H > 0 and last_y < h - SLICE_H:
    yield gray[h - SLICE_H:h]
```

---

#### [P2-13] PaddleOCR 实例每次调用都重新创建

**位置**: 第 69 行

```python
ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
```

**问题**: 同 [P2-09](#p2-09-paddleocr-实例每次调用都重新创建)。

---

#### [P2-14] `extract` 方法无异常处理

**位置**: 第 93-111 行

**问题**: `Image.open(path)` 对于损坏的图片文件会抛异常，但 `extract` 方法没有 try/except，异常会直接传播到调用方。虽然调用方（`build.py`）有异常处理，但适配器自身应提供更友好的错误信息。

---

#### [P3-06] `np.stack([chunk] * 3, axis=-1)` 内存效率低

**位置**: 第 74 行

```python
rgb = np.stack([chunk] * 3, axis=-1)
```

**问题**: 创建了 3 份灰度数组的副本。可使用 `cv2.cvtColor(chunk, cv2.COLOR_GRAY2RGB)` 更高效。

---

### 2.7 tools/dataproc/classifier.py

**文件定位**: 商品类型推断（规则匹配 + conf.yaml 覆盖）  
**代码行数**: 137 行  
**审查结论**: 设计良好，存在 P2 级性能和线程安全问题

---

#### [P2-15] 正则表达式每次调用都重新编译

**位置**: 第 62-64 行、第 74-76 行

```python
for pattern, ptype in _PTYPE_RULES:
    if re.search(pattern, text, re.I):  # ← 每次编译
```

**问题**: `_PTYPE_RULES` 和 `_CATEGORY_RULES` 中的正则模式在每次调用 `classify_ptype` / `classify_category` 时都重新编译。

**修复建议**: 在模块加载时预编译。

```python
_PTYPE_RULES: list = [
    (re.compile(r"羊奶|羊乳|goat", re.I), "羊奶粉"),
    ...
]

def classify_ptype(text: str) -> str:
    if not text:
        return ""
    for pattern, ptype in _PTYPE_RULES:
        if pattern.search(text):
            return ptype
    return ""
```

---

#### [P2-16] 全局缓存无线程安全保护

**位置**: 第 49-51 行

```python
_overrides_cache: dict = {}
_overrides_path: Optional[str] = None
_overrides_mtime: float = 0.0
```

**问题**: `load_category_overrides` 中的 mtime 缓存机制使用全局可变状态，非线程安全。多线程同时调用时可能重复加载文件或产生竞态条件。

**修复建议**: 使用 `threading.Lock` 保护。

---

#### [P2-17] `classify` 中关键词覆盖匹配过于宽泛

**位置**: 第 130-134 行

```python
for kw, cat in overrides.items():
    if kw in text:  # ← 简单子串匹配
        category = cat
        break
```

**问题**: 简单的 `kw in text` 子串匹配可能产生误匹配。例如，若 overrides 中有 `{"奶": "定制类别"}`，则任何包含"奶"字的文本都会匹配。dict 的迭代顺序也不保证一致性（Python 3.7+ 保证插入顺序，但覆盖文件的 key 顺序不可控）。

**修复建议**: 增加最小关键词长度约束，或使用更精确的匹配规则。

---

#### [P3-07] `_PTYPE_RULES` 优先级未显式文档化

**位置**: 第 31-38 行

**问题**: 规则按优先级从高到低排列（"羊奶" 优先于 "牛奶"），但仅通过注释"第一个命中即返回"隐式表达。若有人调整顺序可能引入分类错误。

---

### 2.8 harness/test_admin_api.py

**文件定位**: Admin API 验收测试（18 个检查项）  
**代码行数**: 379 行  
**审查结论**: 覆盖面好，但缺少认证和跨租户测试

---

#### [P1-10] 未测试 Bearer Token 认证

**位置**: 全文

**问题**: 18 个测试项中没有任何一项验证 Bearer Token 认证是否生效。没有测试：
1. 设置 `AGENT_ADMIN_TOKEN` 后，无 token 请求返回 401。
2. 错误 token 返回 401。
3. 正确 token 通过认证。

这是核心安全功能的测试盲区。

**修复建议**: 添加认证测试。

```python
def a19_auth_required():
    """A19: 设置 token 后，无 token 返回 401。"""
    os.environ["AGENT_ADMIN_TOKEN"] = "test-secret-token"
    try:
        client, _ = _make_client(_tmp_db())
        r = client.get("/api/llm")
        assert r.status_code == 401, "无 token 应返回 401"
        r2 = client.get("/api/llm", headers={"Authorization": "Bearer test-secret-token"})
        assert r2.status_code == 200, "正确 token 应返回 200"
    finally:
        os.environ.pop("AGENT_ADMIN_TOKEN", None)
```

---

#### [P1-11] 未测试跨租户越权

**位置**: 全文

**问题**: 未测试 P0-01 和 P0-02 中发现的跨租户越权问题。没有验证 admin 是否能通过 `enterprise_id` 参数访问其他企业数据。

---

#### [P2-18] `a16_scan_no_inbox` 修改全局环境变量

**位置**: 第 314 行

```python
os.environ.pop("BUNDLE_INBOX_DIR", None)
```

**问题**: 测试修改了全局 `os.environ`，可能影响并行运行的其他测试。虽然有 `finally` 清理，但当前代码没有 finally。

**修复建议**: 使用 `unittest.mock.patch.dict` 隔离环境变量修改。

---

#### [P3-08] `_insert_test_product` 注释解释了 Chroma 冲突但未在代码层面避免

**位置**: 第 239-257 行

**问题**: 注释提到"避免在测试中创建第二个 KnowledgeStore（两个 Chroma PersistentClient 在同一目录会导致 'database disk image is malformed'）"，这实际上反映了 [P1-03](#p1-03-_get_store-线程不安全的懒初始化) 中的线程安全问题。测试通过 workaround 绕过了底层设计缺陷。

---

### 2.9 harness/test_ppstructure_table.py

**文件定位**: PP-Structure 表格识别 + 分类器验收（10 个检查项）  
**代码行数**: 212 行  
**审查结论**: 覆盖面合理，存在 P2 级测试隔离问题

---

#### [P2-19] `t9_conf_yaml_override` 不清理 classifier 全局缓存

**位置**: 第 130-150 行

**问题**: 测试通过 `load_category_overrides(conf_path)` 修改了 classifier 模块的全局缓存（`_overrides_cache`, `_overrides_path`, `_overrides_mtime`），但测试结束后未重置。如果后续测试依赖默认的 conf.yaml 路径，可能读到过期的缓存。

**修复建议**: 在测试结束后重置全局缓存。

```python
def t9_conf_yaml_override():
    ...
    # 测试结束后清理
    import dataproc.classifier as cls_mod
    cls_mod._overrides_cache = {}
    cls_mod._overrides_path = None
    cls_mod._overrides_mtime = 0.0
```

---

#### [P2-20] `t8_pdf_test_behavior` 通过子进程运行其他测试

**位置**: 第 118-127 行

```python
result = subprocess.run(
    [sys.executable, test_path],
    capture_output=True, text=True, timeout=30,
    env={**os.environ, "RUN_REAL_OCR": "0"},
)
```

**问题**: 通过子进程运行 `test_dataproc_pdf.py` 增加了测试耦合性和执行时间。若 `test_dataproc_pdf.py` 路径变更或行为修改，此测试会失败。

**修复建议**: 考虑直接导入并调用 `test_dataproc_pdf.main()` 而非子进程。

---

#### [P3-09] `t10_conf_cache` 直接访问私有模块属性

**位置**: 第 156-157 行

```python
from dataproc.classifier import _overrides_path, _overrides_mtime
import dataproc.classifier as cls_mod
```

**问题**: 直接访问以下划线开头的私有属性，测试与实现细节耦合。若缓存实现方式变更，测试需同步修改。

---

### 2.10 harness/test_dataproc_pdf.py

**文件定位**: PDF 适配器验收（I7/I8/I9/I11）  
**代码行数**: 107 行  
**审查结论**: 存在 P1 级安全问题

---

#### [P1-12] 使用已废弃的 `tempfile.mktemp`

**位置**: 第 55、65、78 行

```python
p = tempfile.mktemp(suffix=".pdf")
```

**问题**: `tempfile.mktemp` 已被 Python 官方废弃（自 Python 2.3 起），存在 TOCTOU 竞态条件：攻击者可在 `mktemp` 返回路径和文件实际创建之间创建符号链接，导致写入意外文件。

注意：同项目的 `test_ppstructure_table.py` 第 105 行已使用更安全的 `tempfile.mkstemp`，说明开发者知晓正确做法但未在此文件中应用。

**修复建议**: 使用 `tempfile.NamedTemporaryFile` 或 `tempfile.mkstemp`。

```python
fd, p = tempfile.mkstemp(suffix=".pdf")
os.close(fd)
# ... 使用 p ...
os.unlink(p)
```

---

#### [P1-13] 测试失败时临时文件未清理

**位置**: 第 55-62、65-74、78-93 行

```python
p = tempfile.mktemp(suffix=".pdf")
_digital_pdf(p, "...")
r = PDFAdapter().extract(p, run_real_ocr=False)
if not r.text or ...:
    fails.append(...)  # ← 若此处跳到下一个测试，os.unlink(p) 不会执行
    # 没有 else 分支保护
os.unlink(p)  # ← 仅在正常流程下执行
```

**问题**: 当断言失败（`fails.append`）时，代码继续执行 `os.unlink(p)`（因为没有 `return` 或 `raise`），但如果 `_digital_pdf` 或 `extract` 抛异常，`os.unlink` 不会执行，临时文件泄露。

**修复建议**: 使用 `try/finally` 或上下文管理器。

```python
p = tempfile.mktemp(suffix=".pdf")
try:
    _digital_pdf(p, "...")
    r = PDFAdapter().extract(p, run_real_ocr=False)
    if not r.text or r.meta.get("is_scanned") or r.meta.get("ocr"):
        fails.append(f"I7: ...")
    else:
        print("[PASS] I7")
finally:
    if os.path.exists(p):
        os.unlink(p)
```

---

#### [P2-21] `_digital_pdf` 和 `_scanned_pdf` 不关闭 fitz 文档

**位置**: 第 29-33、36-41 行

```python
def _digital_pdf(path, text):
    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((72, 72), text)
    doc.save(path)
    # ← doc 未关闭
```

**问题**: 同 [P1-07](#p1-07-_render_pages-不关闭-fitz-文档对象)，测试辅助函数也未关闭 fitz 文档。

---

#### [P3-10] 无损坏/无效 PDF 的测试用例

**位置**: 全文

**问题**: 仅测试了正常数字 PDF 和空白扫描件 PDF，未测试损坏文件、加密 PDF、超大 PDF 等边界情况。

---

### 2.11 src/kb/store.py

**文件定位**: 知识库存储（三库模型，Chroma + SQLite）  
**代码行数**: 626 行  
**审查结论**: 架构设计优秀，但存在 P0 跨租户问题和 P1 连接泄露

---

#### [P0-04] `confirm_product` / `delete_product` 无 enterprise_id 隔离

**位置**: 第 206-237 行

```python
def confirm_product(self, product_id: int, value: str,
                    table: str = "products_milk") -> None:
    col = self._PENDING_COL[table]
    with connect(self.db_path) as conn:
        conn.execute(
            f"UPDATE {table} SET {col}=? WHERE id=?",
            (value, product_id),  # ← 无 enterprise_id 条件
        )

def delete_product(self, product_id: int, table: str = "products_milk") -> None:
    ...
    cur.execute(f"DELETE FROM {table} WHERE id=?", (product_id,))  # ← 无 enterprise_id
```

**问题**: 两个方法都仅按 `product_id` 操作，不校验 `enterprise_id`。在共享数据库模式下（多企业共用同一 SQLite 实例），admin 可通过枚举 `product_id` 修改或删除其他企业的商品。`delete_product` 还会连带删除关联的 corpus 记录和 Chroma 向量，造成不可逆的数据破坏。

**修复建议**: 增加 `enterprise_id` 参数并在 SQL 中过滤。

```python
def confirm_product(self, product_id: int, value: str,
                    table: str = "products_milk", enterprise_id: str = "") -> None:
    col = self._PENDING_COL[table]
    with connect(self.db_path) as conn:
        conn.execute(
            f"UPDATE {table} SET {col}=? WHERE id=? AND enterprise_id=?",
            (value, product_id, enterprise_id),
        )
```

---

#### [P1-14] SQLite 连接泄露（与 server.py 相同问题）

**位置**: 第 76、152、162、176、194、212、223、244、255、263、281、299、330、357、457、507、532 行（所有 `with connect(...) as conn:` 调用）

**问题**: 同 [P1-02](#p1-02-sqlite-连接泄露--with-connect-as-conn-不关闭连接)。`sqlite3.Connection` 的上下文管理器仅管理事务，不关闭连接。整个 `store.py` 有 17+ 处 `with connect()` 调用，全部存在连接泄露。

**修复建议**: 统一使用 `db_tx` 上下文管理器。

---

#### [P2-22] `list_pending_products` 在循环中多次打开连接

**位置**: 第 192-204 行

```python
for tbl, col in self._PENDING_COL.items():
    with connect(self.db_path) as conn:  # ← 每次循环都开新连接
        for r in conn.execute(...).fetchall():
            ...
```

**问题**: 循环遍历 2 个表，每次都打开新连接。应使用单个连接。

**修复建议**:

```python
with connect(self.db_path) as conn:
    for tbl, col in self._PENDING_COL.items():
        for r in conn.execute(...).fetchall():
            ...
```

---

#### [P2-23] `retrieve` 方法过长（145 行）

**位置**: 第 464-609 行

**问题**: `retrieve` 方法承担了向量召回、FTS 召回、RRF 融合、回查 corpus、重排、分组、加权、截断等多个职责，总计 145 行。可读性和可维护性差。

**修复建议**: 拆分为 `_chroma_recall`、`_fts_recall`、`_fuse_and_rerank`、`_group_and_score` 等子方法。

---

#### [P2-24] `delete_product` 逐条删除 Chroma 向量

**位置**: 第 233-237 行

```python
for i in ids:
    try:
        self.collection.delete(ids=[str(i)])
    except Exception:
        pass
```

**问题**: 逐条删除效率低，Chroma API 支持批量删除。

**修复建议**: 批量删除。

```python
if ids:
    try:
        self.collection.delete(ids=[str(i) for i in ids])
    except Exception:
        pass
```

---

#### [P2-25] `update_corpus` 在连接关闭后访问 Row 对象

**位置**: 第 382-386 行

```python
    # with connect 块结束后
    upd_kind = (json.loads(new_meta).get("kind", "") if new_meta else "")
    self._index(
        None, cid, new_title, new_content, row["enterprise_id"], row["part"],  # ← row 在连接关闭后使用
        product_id=row["product_id"], chunk=row["chunk"] or "", kind=upd_kind,
    )
```

**问题**: `row` 是 `sqlite3.Row` 对象，在 `with connect()` 块结束后访问。虽然 `sqlite3.Row` 在连接关闭后仍可访问（数据已加载到内存），但这不是一种好的实践，且未来 Python 版本可能改变此行为。

**修复建议**: 在 `with` 块内提取所需值。

---

#### [P3-11] `_filter_product_ids` 中 filters key 与列名校验逻辑可简化

**位置**: 第 441-462 行

**问题**: 遍历两个表的所有列集合来检查 filters 是否匹配，逻辑可简化为先确定目标表再查询。

---

### 2.12 tools/dataproc/build.py

**文件定位**: 数据构建流程（仓库资料 → NDJSON bundle）  
**代码行数**: 294 行  
**审查结论**: 存在 P1 级文件句柄泄露和 P1 级硬编码 kind 问题

---

#### [P1-15] 文件句柄泄露 — `open()` 未关闭

**位置**: 第 96 行、第 169 行

```python
# 第 96 行
text = open(path, encoding="utf-8", errors="ignore").read()

# 第 169 行
content = open(full_path, encoding="utf-8", errors="ignore").read()
```

**问题**: `open()` 返回的文件对象未被显式关闭。虽然 CPython 的引用计数会在 `.read()` 返回后立即关闭文件，但这不是保证行为（PyPy 等实现不使用引用计数）。在批量处理大量文件时可能导致文件句柄耗尽。

**修复建议**: 使用 `with` 语句。

```python
with open(path, encoding="utf-8", errors="ignore") as f:
    text = f.read()
```

---

#### [P1-16] `ProductRecord` 的 kind 硬编码为 "milk"

**位置**: 第 193 行、第 236 行

```python
# 第 193 行（_process_nontext 中）
product_dict = ProductRecord(
    kind="milk", ...  # ← 营养品也被标记为 milk
).to_dict()

# 第 236 行（build_bundle 中）
products.append(ProductRecord(
    kind="milk", ...  # ← 同上
).to_dict())
```

**问题**: 所有商品都被标记为 `kind="milk"`，包括营养品。这意味着营养品的 ProductRecord 类型被错误标记，后续导入知识库时可能被错误地插入 `products_milk` 表而非 `products_nutrition` 表。

**修复建议**: 根据分类结果或文件路径推断 kind。

```python
# 使用 classifier 的 product_category 结果
kind = "nutrition" if cls.get("product_category") == "营养品" else "milk"
product_dict = ProductRecord(
    kind=kind, ...
).to_dict()
```

---

#### [P2-26] `build_bundle` 函数过长

**位置**: 第 205-294 行

**问题**: `build_bundle` 函数约 90 行，包含文件遍历、内容解析、产品入库、bundle 写入等多个职责。

**修复建议**: 拆分为 `_process_product_md`、`_process_other_files`、`_write_bundle` 等子函数。

---

#### [P2-27] `expand_selection` 被调用两次

**位置**: 第 220 行和第 292 行

```python
# 第 220 行
selected = set(expand_selection(repo_dir, selection)) if selection is not None else None

# 第 292 行
processed_files = expand_selection(repo_dir, selection) if selection is not None else ...
```

**问题**: 当 `selection is not None` 时，`expand_selection` 被调用两次，重复遍历文件系统。

**修复建议**: 第一次调用时保存结果并复用。

```python
expanded = expand_selection(repo_dir, selection) if selection is not None else None
selected = set(expanded) if expanded is not None else None
# ...
processed_files = expanded if expanded is not None else [r for top in TOP_FOLDERS for r in _walk_top(repo_dir, top)]
```

---

#### [P2-28] `_parse_md_product` 和 `_process_nontext` 无错误处理

**位置**: 第 95-106 行、第 133-201 行

**问题**: 文件读取、正则匹配、JSON 解析等操作均无 try/except。单个文件解析失败会中断整个构建流程。

**修复建议**: 在 `build_bundle` 的文件遍历循环中增加 per-file 异常捕获，记录错误并继续处理其他文件。

---

#### [P3-12] `_count_by_kind` 可用 `collections.Counter` 简化

**位置**: 第 50-54 行

```python
def _count_by_kind(corpus: List[dict]) -> dict:
    out: dict = {}
    for c in corpus:
        out[c["kind"]] = out.get(c["kind"], 0) + 1
    return out
```

**修复建议**:

```python
from collections import Counter

def _count_by_kind(corpus: List[dict]) -> dict:
    return dict(Counter(c["kind"] for c in corpus))
```

---

## 3. 问题汇总

### P0 — 安全漏洞（4 项）

| 编号 | 文件 | 行号 | 问题 |
|------|------|------|------|
| P0-01 | server.py | 237, 272, 316 | 跨租户数据越权 — list_employees/list_gateway_bindings/list_babies 允许传入任意 enterprise_id |
| P0-02 | server.py + store.py | 196-214, 206-237 | 跨租户数据修改 — confirm_product/delete_product 无 enterprise_id 校验 |
| P0-03 | pages.py | 141 | XSS — innerHTML 直接插入 API 响应 data.message |
| P0-04 | store.py | 206-237 | confirm_product/delete_product 无 enterprise_id 隔离（P0-02 的 store 侧根因） |

### P1 — 功能缺陷（12 项）

| 编号 | 文件 | 行号 | 问题 |
|------|------|------|------|
| P1-01 | server.py | 69 | Token 比较使用非常量时间比较（时序攻击） |
| P1-02 | server.py | 多处 | SQLite 连接泄露 — with connect() 不关闭连接 |
| P1-03 | server.py | 91-101 | _get_store() 线程不安全的懒初始化 |
| P1-04 | server.py | 74-82 | YAML 路径验证与文档声明不符（路径遍历风险） |
| P1-05 | server.py | 157 | API Key 明文写入 YAML 文件 |
| P1-06 | pages.py | 44-46, 261, 317, 354 | 服务端与客户端 XSS 转义策略不一致 |
| P1-07 | pdf.py | 24-31 | _render_pages 不关闭 fitz 文档对象 |
| P1-08 | pdf.py | 49-58 | _ocr_images 中 OCR 结果解包无防御 |
| P1-09 | image_table.py | 95 | PIL Image 文件句柄未关闭 |
| P1-10 | test_admin_api.py | 全文 | 未测试 Bearer Token 认证 |
| P1-11 | test_admin_api.py | 全文 | 未测试跨租户越权 |
| P1-12 | test_dataproc_pdf.py | 55, 65, 78 | 使用已废弃的 tempfile.mktemp |
| P1-13 | test_dataproc_pdf.py | 55-93 | 测试失败时临时文件未清理 |
| P1-14 | store.py | 多处 | SQLite 连接泄露（17+ 处） |
| P1-15 | build.py | 96, 169 | 文件句柄泄露 — open() 未关闭 |
| P1-16 | build.py | 193, 236 | ProductRecord kind 硬编码为 "milk" |

### P2 — 代码质量（18 项）

| 编号 | 文件 | 行号 | 问题 |
|------|------|------|------|
| P2-01 | server.py | 26 | secrets 模块导入但未使用 |
| P2-02 | server.py | 全文 | 缺少安全事件日志 |
| P2-03 | models.py | 75-79 | mask_token 脱敏力度不足 |
| P2-04 | models.py | 40-65 | Pydantic 模型缺少输入校验 |
| P2-05 | pages.py | 261, 317, 354 | esc() 函数重复定义 3 次 |
| P2-06 | pages.py | 176-184 | loadStatus innerHTML 拼接不够健壮 |
| P2-07 | _ppstructure.py | 51-52 | 全局可变状态线程不安全 |
| P2-08 | _ppstructure.py | 20-47 | TableHTMLParser 不处理 colspan/rowspan |
| P2-09 | pdf.py | 43 | PaddleOCR 实例每次重新创建 |
| P2-10 | pdf.py | 20-21, 94-99 | 异常静默吞掉无日志 |
| P2-11 | pdf.py | 76-91 | extract 无文件存在性检查 |
| P2-12 | image_table.py | 39-49 | _slice_long 末尾切片可能重复 |
| P2-13 | image_table.py | 69 | PaddleOCR 实例每次重新创建 |
| P2-14 | image_table.py | 93-111 | extract 无异常处理 |
| P2-15 | classifier.py | 62-64, 74-76 | 正则表达式每次重新编译 |
| P2-16 | classifier.py | 49-51 | 全局缓存无线程安全保护 |
| P2-17 | classifier.py | 130-134 | 关键词覆盖匹配过于宽泛 |
| P2-18 | test_admin_api.py | 314 | 测试修改全局环境变量 |
| P2-19 | test_ppstructure_table.py | 130-150 | 测试不清理 classifier 全局缓存 |
| P2-20 | test_ppstructure_table.py | 118-127 | 通过子进程运行其他测试 |
| P2-21 | test_dataproc_pdf.py | 29-41 | 测试辅助函数不关闭 fitz 文档 |
| P2-22 | store.py | 192-204 | list_pending_products 循环中多次打开连接 |
| P2-23 | store.py | 464-609 | retrieve 方法过长（145 行） |
| P2-24 | store.py | 233-237 | delete_product 逐条删除 Chroma 向量 |
| P2-25 | store.py | 382-386 | update_corpus 连接关闭后访问 Row |
| P2-26 | build.py | 205-294 | build_bundle 函数过长 |
| P2-27 | build.py | 220, 292 | expand_selection 被调用两次 |
| P2-28 | build.py | 95-106, 133-201 | 文件解析无错误处理 |

### P3 — 建议优化（12 项）

| 编号 | 文件 | 行号 | 问题 |
|------|------|------|------|
| P3-01 | server.py | 58-61 | _get_admin_token 每次请求读取环境变量 |
| P3-02 | models.py | 52 | db_path 默认值为相对路径 |
| P3-03 | pages.py | 10-41 | CSS 嵌入 Python 字符串 |
| P3-04 | _ppstructure.py | 80 | extract_tables 参数缺少类型注解 |
| P3-05 | _ppstructure.py | 121-125 | reset() 缺少使用场景文档 |
| P3-06 | image_table.py | 74 | np.stack 内存效率低 |
| P3-07 | classifier.py | 31-38 | _PTYPE_RULES 优先级未显式文档化 |
| P3-08 | test_admin_api.py | 239-257 | _insert_test_product 注释反映底层设计缺陷 |
| P3-09 | test_ppstructure_table.py | 156-157 | 直接访问私有模块属性 |
| P3-10 | test_dataproc_pdf.py | 全文 | 无损坏/无效 PDF 测试用例 |
| P3-11 | store.py | 441-462 | _filter_product_ids 校验逻辑可简化 |
| P3-12 | build.py | 50-54 | _count_by_kind 可用 Counter 简化 |

---

## 4. 总体评分与总结

### 总体评分: 3.0 / 5.0

### 评分依据

| 维度 | 评分 | 说明 |
|------|------|------|
| 安全性 | 2.5 | 有安全设计意识（Token 认证、表名白名单、HTML 转义、敏感字段过滤），但跨租户越权问题严重（4 个 P0），时序攻击、路径遍历等问题并存 |
| 功能正确性 | 3.0 | 核心功能逻辑正确，但 ProductRecord kind 硬编码可能导致营养品数据错误入库，连接泄露可能导致服务不稳定 |
| 代码质量 | 3.5 | 模块拆分清晰，注释和文档字符串较完善，但存在代码重复、方法过长、正则未预编译等问题 |
| 测试覆盖 | 3.0 | 28 个测试项覆盖面广，但缺少认证测试和跨租户越权测试（安全测试盲区），测试隔离性不足 |
| 可维护性 | 3.0 | 全局可变状态较多，线程安全问题普遍，retrieve 方法过长，CSS/JS 嵌入 Python 字符串 |

### 总结

**优点**:
1. 安全设计意识好：Bearer Token 认证机制、表名白名单（`ALLOWED_TABLES`）、bot_token 脱敏、宝宝档案敏感字段过滤（allergens/medical_history 等不返回）。
2. 模块拆分合理：admin 模块拆分为 server/models/pages 三层，PP-Structure 共享模块消除了 pdf.py/image_table.py 的重复代码。
3. 优雅降级设计：paddleocr/fitz/cv2 等重依赖缺失时返回空/None/占位，不阻断流程。
4. 测试覆盖面广：admin API 18 项 + PP-Structure/分类器 10 项 + PDF 适配器 4 项，核心路径有验收。
5. HQ 只读护栏（`_row_readonly`）设计合理，防止实例误改厂商分发数据。

**需优先修复的问题**:
1. **P0 跨租户越权**（P0-01, P0-02, P0-04）: 这是上线前必须修复的阻塞性问题。在 ToB 多租户场景下，任何已认证 admin 都能访问/修改其他企业的数据，违反了最基本的租户隔离原则。
2. **P0 XSS**（P0-03, P0-04）: 虽然当前不可直接利用（message 为硬编码、table 为白名单值），但代码模式危险，违反了文件自身的安全设计原则，后续迭代极易引入可利用的 XSS。
3. **P1 连接泄露**（P1-02, P1-14）: 全项目 20+ 处 `with connect() as conn:` 均不关闭连接，长时间运行的服务会因文件描述符耗尽或 SQLite 锁争用而崩溃。建议统一使用已有的 `db_tx` 上下文管理器。
4. **P1 资源泄露**（P1-07, P1-09, P1-15）: fitz 文档、PIL Image、文件句柄均未正确关闭，在批量处理场景下会导致资源耗尽。
5. **P1 ProductRecord kind 硬编码**（P1-16）: 营养品被标记为 "milk"，可能导致数据入库到错误的表。

**建议的修复优先级**:
1. 第一优先：P0-01, P0-02, P0-04（跨租户越权）→ 添加 enterprise_id 校验
2. 第二优先：P0-03（XSS）→ 修复 innerHTML 和转义逻辑
3. 第三优先：P1-02, P1-14（连接泄露）→ 统一使用 db_tx
4. 第四优先：P1-01（时序攻击）、P1-03（线程安全）、P1-07/09/15（资源泄露）
5. 第五优先：P1-16（kind 硬编码）、P1-10/11（安全测试补充）
6. 后续迭代：P2 和 P3 问题

---

*报告生成时间: 2026-07-22*  
*审查文件数: 12 (+ 2 支撑文件)*  
*发现问题总计: P0=4, P1=12, P2=18, P3=12*
