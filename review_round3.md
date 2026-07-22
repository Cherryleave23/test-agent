# 代码审查报告（第三轮 / 修复验证轮）

**项目**: 母婴垂类 ToB RAG Agent  
**审查范围**: 第二轮发现的 P0=4, P1=12, P2=18 修复验证 + 新问题发现  
**审查日期**: 2026-07-22  
**审查标准**: P0(安全) / P1(功能缺陷) / P2(代码质量) / P3(建议优化)  

---

## 目录

1. [修复验证总览](#1-修复验证总览)
2. [逐文件修复验证](#2-逐文件修复验证)
3. [新发现问题](#3-新发现问题)
4. [问题汇总](#4-问题汇总)
5. [总体评分与总结](#5-总体评分与总结)

---

## 1. 修复验证总览

### 修复统计

| 级别 | 上轮数量 | 完全修复 | 部分修复 | 未修复 | 修复率 |
|------|----------|----------|----------|--------|--------|
| P0   | 4        | 4        | 0        | 0      | 100%   |
| P1   | 12       | 9        | 3        | 0      | 75%    |
| P2   | 18       | 13       | 3        | 2      | 72%    |
| 合计 | 34       | 26       | 6        | 2      | 76%    |

**结论**: 全部 4 个 P0 安全漏洞已正确修复，P1 和 P2 修复质量良好但有部分修复不完整，且修复过程中引入了若干新问题。

### 新发现问题统计

| 级别 | 数量 | 说明 |
|------|------|------|
| P0   | 0    | 无新增安全漏洞 |
| P1   | 5    | 跨租户遗漏、线程安全、异常处理 |
| P2   | 7    | 代码重复、路径校验、数据一致性等 |
| P3   | 5    | 前端错误处理、冗余提交等 |
| 合计 | 17   | — |

---

## 2. 逐文件修复验证

### 2.1 src/admin/server.py

#### P0-01 修复验证: 跨租户数据越权（list_employees / list_gateway_bindings / list_babies）— 已修复

**位置**: 第 249-264 行、第 283-298 行、第 328-350 行

三个端点均已移除外部传入的 `enterprise_id` 参数，强制使用 `cfg.enterprise_id`：

```python
# 第 252 行（list_employees）
ent = cfg.enterprise_id  # 强制使用当前实例的企业 ID

# 第 286 行（list_gateway_bindings）
ent = cfg.enterprise_id  # 同上

# 第 332 行（list_babies）
ent = cfg.enterprise_id  # 同上
```

SQL 查询均加入了 `WHERE enterprise_id=?` 过滤条件。**修复正确、完整。**

#### P0-02 修复验证: 跨租户数据修改（confirm_product / delete_product）— 已修复

**位置**: 第 208-228 行（server.py）+ 第 207-269 行（store.py）

server.py 现在传递 `cfg.enterprise_id`：
```python
# 第 215 行
store.confirm_product(product_id, value, table, cfg.enterprise_id)
# 第 226 行
store.delete_product(product_id, table, cfg.enterprise_id)
```

store.py 现在接受 `enterprise_id` 参数并在操作前校验归属：
```python
# 第 218-227 行
if enterprise_id is not None:
    row = conn.execute(
        f"SELECT enterprise_id FROM {table} WHERE id=?", (product_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"商品不存在: id={product_id}")
    if row["enterprise_id"] != enterprise_id:
        raise PermissionError(
            f"跨租户越权: 商品 id={product_id} 不属于 enterprise_id={enterprise_id}"
        )
```

**修复正确、完整。** 校验在同一个 `db_tx` 事务内完成，不存在 TOCTOU 竞态风险。

#### P1-01 修复验证: Token 时序攻击 — 已修复

**位置**: 第 68 行

```python
if credentials is None or not secrets.compare_digest(credentials.credentials, expected):
```

使用 `secrets.compare_digest` 进行常量时间比较，`secrets` 模块已正确导入并使用。**修复正确。**

#### P1-02 修复验证: SQLite 连接泄露 — 已修复

**位置**: 全文（第 179、233、241、253、268、278、287、302、319 行）

所有 `with connect(...)` 已替换为 `with db_tx(...)`。`db_tx` 上下文管理器在 `finally` 中调用 `conn.close()`，确保连接关闭。**修复正确、完整。**

#### P1-03 修复验证: _get_store() 线程安全 — 已修复

**位置**: 第 97-109 行

```python
_store_lock = threading.Lock()

def _get_store() -> KnowledgeStore:
    if _store_holder["store"] is None:
        with _store_lock:
            if _store_holder["store"] is None:  # double-check
                _store_holder["store"] = KnowledgeStore(...)
    return _store_holder["store"]
```

双重检查锁定模式正确实现。**修复正确。**

#### P1-04 修复验证: YAML 路径验证 — 部分修复

**位置**: 第 74-88 行

新增了 `..` 路径遍历检查和空路径检查，文档注释也已修正为准确描述实现行为。但绝对路径（如 `/etc/cron.d/evil.yaml`）仍然可以通过验证。考虑到路径来源为 `AGENT_CONFIG_PATH` 环境变量（非 API 用户直接输入），此风险可接受。**部分修复，可接受。**

#### P1-05 修复验证: API Key 明文写入 — 部分修复

**位置**: 第 166-167 行

空 `api_key` 不再覆盖已有 key：
```python
if update.api_key:
    data["llm"]["api_key"] = update.api_key
```

此部分已修复。但 API Key 仍以明文存储在 YAML 文件中，未迁移到环境变量或加密存储。**部分修复。**

#### P2-01 修复验证: secrets 模块未使用 — 已修复

`secrets` 模块现在在第 68 行的 `secrets.compare_digest` 中使用。**修复正确。**

#### P2-02 修复验证: 缺少安全事件日志 — 已修复

新增了 `logger = logging.getLogger(__name__)`（第 57 行）和多处日志调用：
- 第 69 行：认证失败日志
- 第 172 行：LLM 配置更新日志
- 第 200 行：bundle 扫描日志
- 第 216 行：商品确认日志
- 第 227 行：商品删除日志
- 第 314 行：网关绑定日志
- 第 324 行：网关解绑日志

**修复正确。**

---

### 2.2 src/admin/models.py

#### P2-03 修复验证: mask_token 脱敏力度 — 已修复

**位置**: 第 83-92 行

```python
def mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 12:
        return token[:2] + "*" * (len(token) - 2)
    return token[:4] + "*" * (len(token) - 8) + token[-4:]
```

短 token 保留前 2 字符，长 token 保留前 4 + 后 4 字符，中间用星号填充。脱敏力度合理。**修复正确。**

#### P2-04 修复验证: Pydantic 模型输入校验 — 部分修复

**位置**: 第 40-73 行

- `LLMConfigUpdate`: 添加了 `field_validator` 验证 `kind` 白名单，`Field` 约束 `temperature`（0.0-2.0）和 `max_tokens`（1-32768）。**已修复。**
- `StoreCreate`: 添加了 `Field(min_length=1)` 约束 `enterprise_id` 和 `enterprise_name`。但 `db_path` 字段仍无路径校验（接受 `../../etc/malicious.db` 等路径）。**部分修复。**
- `EmployeeCreate` / `GatewayBinding`: 添加了 `Field(min_length=1)` 基本约束。**已修复（基本）。**

---

### 2.3 src/admin/pages.py

#### P0-03 修复验证: XSS — innerHTML 直接插入 API 响应 — 已修复

**位置**: 第 145-151 行

```javascript
const card = document.createElement('div');
card.className = 'card';
card.style.background = '#d1fae5';
card.textContent = data.message;
const result = document.getElementById('result');
result.innerHTML = '';
result.appendChild(card);
```

使用 `textContent` 替代 `innerHTML` 拼接，并通过 `createElement` + `appendChild` 安全插入 DOM。**修复正确。**

#### P0-04 修复验证: XSS — 商品列表转义不完整 + table 参数注入 — 已修复

**位置**: 第 203-209 行

```javascript
const safeName = esc(p.name);
const safeBrand = esc(p.brand);
const safeTable = esc(p.table);
html += `<tr><td>${p.id}</td><td>${safeName}</td><td>${safeBrand}</td><td>${safeTable}</td>
  <td><input id="val-${p.id}" placeholder="注册号/批准文号" style="width:160px;">
  <button onclick="confirmProduct(${p.id},'${safeTable}')" class="btn">确认</button>
  <button onclick="deleteProduct(${p.id},'${safeTable}')" class="btn btn-danger">删除</button></td></tr>`;
```

所有动态值均通过 `esc()` 函数转义。**修复正确。**

#### P1-06 修复验证: 服务端与客户端 XSS 转义策略不一致 — 已修复

**位置**: 第 43-45 行

```javascript
_BASE_JS = """
function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
"""
```

提取了公共 `_BASE_JS`，统一定义 `esc()` 函数，转义全部 5 个 HTML 特殊字符（`&`, `<`, `>`, `"`, `'`）。所有页面均引入 `{_BASE_JS}` 并使用 `esc()`。服务端 `_esc()` 使用 `html.escape()` 也转义相同字符集。**修复正确、完整。**

#### P2-05 修复验证: esc() 函数重复定义 — 已修复

`esc()` 函数已提取到 `_BASE_JS`（第 43-45 行），在所有页面中通过 `{_BASE_JS}` 引入，不再重复定义。**修复正确。**

#### P2-06 修复验证: loadStatus innerHTML 拼接 — 已修复

**位置**: 第 183-195 行

动态值改用 `textContent` 安全设置，`innerHTML` 仅用于静态模板结构。**修复正确。**

---

### 2.4 src/kb/store.py

#### P0-04 修复验证: confirm_product / delete_product 无 enterprise_id 隔离 — 已修复

**位置**: 第 207-269 行

两个方法均接受 `enterprise_id` 参数，在操作前校验商品归属。跨租户操作抛出 `PermissionError`。**修复正确、完整。**

#### P1-14 修复验证: SQLite 连接泄露 — 已修复

**位置**: 全文

所有 `with connect(self.db_path) as conn:` 已替换为 `with db_tx(self.db_path) as conn:`。`db_tx` 确保连接在 `finally` 中关闭。**修复正确、完整。**

#### P2-22 修复验证: list_pending_products 循环中多次打开连接 — 已修复

**位置**: 第 192-205 行

改为使用单个连接遍历两张表：
```python
with db_tx(self.db_path) as conn:
    for tbl, col in self._PENDING_COL.items():
        for r in conn.execute(...).fetchall():
            ...
```

**修复正确。**

#### P2-24 修复验证: delete_product 逐条删除 Chroma 向量 — 已修复

**位置**: 第 264-269 行

```python
if ids:
    try:
        self.collection.delete(ids=[str(i) for i in ids])
    except Exception:
        pass
```

改为批量删除。**修复正确。**

#### P2-25 修复验证: update_corpus 在连接关闭后访问 Row — 已修复

**位置**: 第 413-417 行

```python
# P2-25: 在连接关闭前提取 Row 值，避免连接关闭后访问
row_ent = row["enterprise_id"]
row_part = row["part"]
row_pid = row["product_id"]
row_chunk = row["chunk"] or ""
```

Row 值在 `with` 块内提前提取。**修复正确。**

---

### 2.5 tools/dataproc/adapters/_ppstructure.py

#### P2-07 修复验证: 全局可变状态线程安全 — 已修复

**位置**: 第 87 行、第 96-117 行

```python
_pp_lock = threading.Lock()

def get_ppstructure():
    global _pp_engine, _pp_initialized
    if _pp_initialized:
        return _pp_engine
    with _pp_lock:
        if _pp_initialized:
            return _pp_engine
        _pp_initialized = True
        # ... 初始化 ...
    return _pp_engine
```

双重检查锁定模式正确实现。**修复正确。**

#### P2-08 修复验证: TableHTMLParser 不处理 colspan/rowspan — 已修复

**位置**: 第 21-81 行

完全重写了 `TableHTMLParser`，通过 `_occupied` 集合跟踪已被占用的 `(row, col)` 位置，在 `handle_starttag` 中解析 `colspan`/`rowspan` 属性并跳过已占用的列，在 `handle_endtag` 中将值填充到跨度覆盖的所有位置。`_int_attr` 辅助方法防御性地处理无效属性值。**修复正确、完整。**

---

### 2.6 tools/dataproc/adapters/pdf.py

#### P1-07 修复验证: _render_pages 不关闭 fitz 文档 — 已修复

**位置**: 第 51-57 行

```python
doc = fitz.open(path)
try:
    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        yield Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
finally:
    doc.close()
```

使用 `try/finally` 确保文档关闭。**修复正确。**

#### P1-08 修复验证: OCR 结果解包无防御 — 已修复

**位置**: 第 84-88 行

```python
try:
    box, (txt, score) = line
except (ValueError, TypeError):
    logger.warning("跳过无法解析的 OCR 行: %r", line)
    continue
```

**修复正确。**

#### P2-09 修复验证: PaddleOCR 实例每次重新创建 — 部分修复

**位置**: 第 12-29 行

添加了模块级单例 `_ocr_engine` 和 `_get_paddle_ocr()` 函数。但**未使用 `threading.Lock` 保护**，与 `_ppstructure.py` 的正确实现不同。多线程同时调用时可能创建多个 PaddleOCR 实例。详见新发现问题 [P1-N3](#p1-n3-paddleocr-单例在-pdfpy-和-image_tablepy-中非线程安全)。**部分修复（引入新问题）。**

#### P2-10 修复验证: 异常静默吞掉无日志 — 已修复

**位置**: 第 42-43 行、第 131-132 行

```python
except Exception as e:
    logger.warning("pypdf 文本抽取失败 %s: %s", type(e).__name__, e)
    return ""
```

两处异常处理均添加了 `logger.warning` 日志。**修复正确。**

#### P2-11 修复验证: extract 无文件存在性检查 — 已修复

**位置**: 第 108-109 行

```python
if not os.path.isfile(path):
    raise FileNotFoundError(f"PDF 文件不存在: {path}")
```

**修复正确。**

---

### 2.7 tools/dataproc/adapters/image_table.py

#### P1-09 修复验证: PIL Image 文件句柄未关闭 — 已修复

**位置**: 第 118-120 行

```python
with Image.open(path) as pil:
    arr = np.array(pil.convert("RGB"))
```

**修复正确。**

#### P2-12 修复验证: _slice_long 末尾切片重复 — 已修复

**位置**: 第 64-71 行

```python
last_y = 0
for y in range(0, max(h - SLICE_H, 0) + 1, step):
    yield gray[y:y + SLICE_H]
    last_y = y
if h - SLICE_H > last_y:
    yield gray[h - SLICE_H:h]
```

通过跟踪 `last_y` 避免末尾切片与循环最后一片完全重复。经多组参数验证（h=1200/2280/2400/3000），逻辑正确。**修复正确。**

#### P2-13 修复验证: PaddleOCR 实例每次重新创建 — 部分修复

**位置**: 第 19-36 行

与 pdf.py 相同的单例模式，但同样**未使用 `threading.Lock`**。详见 [P1-N3](#p1-n3-paddleocr-单例在-pdfpy-和-image_tablepy-中非线程安全)。**部分修复（引入新问题）。**

#### P2-14 修复验证: extract 无异常处理 — 已修复

**位置**: 第 116-131 行

```python
try:
    with Image.open(path) as pil:
        arr = np.array(pil.convert("RGB"))
    # ...
except (OCRDeferred, OCRDependencyMissing):
    raise
except Exception as e:
    logger.exception("图片适配器处理失败 %s: %s", type(e).__name__, e)
    raise RuntimeError(f"图片处理失败: {type(e).__name__}: {e}") from e
```

OCR 相关异常透传，其他异常包装为 `RuntimeError` 并记录日志。**修复正确。**

---

### 2.8 tools/dataproc/classifier.py

#### P2-15 修复验证: 正则表达式每次重新编译 — 已修复

**位置**: 第 32-47 行

```python
_PTYPE_RULES: list = [
    (re.compile(r"羊奶|羊乳|goat", re.I), "羊奶粉"),
    # ...
]
```

所有正则模式在模块加载时预编译。`classify_ptype` 和 `classify_category` 使用 `regex.search(text)` 而非 `re.search(pattern, text)`。**修复正确。**

#### P2-16 修复验证: 全局缓存无线程安全保护 — 已修复

**位置**: 第 53 行、第 107-123 行

```python
_overrides_lock = threading.Lock()

# 读缓存
with _overrides_lock:
    if conf_path == _overrides_path and mtime == _overrides_mtime:
        return _overrides_cache
# 写缓存
with _overrides_lock:
    _overrides_cache = new_cache
    _overrides_path = conf_path
    _overrides_mtime = mtime
```

锁保护读和写操作，IO 操作放在锁外避免阻塞。**修复正确。**

#### P2-17 修复验证: 关键词覆盖匹配过于宽泛 — 已修复

**位置**: 第 140 行

```python
if len(kw) >= 2 and kw in text:
```

添加了最小关键词长度约束（`>= 2`），避免单字误匹配。**修复正确。**

---

### 2.9 tools/dataproc/build.py

#### P1-15 修复验证: 文件句柄泄露 — 已修复

**位置**: 第 99 行、第 173 行

```python
with open(path, encoding="utf-8", errors="ignore") as f:
    text = f.read()
```

两处 `open()` 均改为 `with` 语句。**修复正确。**

#### P1-16 修复验证: ProductRecord kind 硬编码 — 已修复（ProductRecord），未修复（HQProductRecord）

**位置**: 第 198 行、第 251 行

ProductRecord 的 kind 现在根据分类结果推断：
```python
prod_kind = "nutrition" if cls.get("product_category") == "营养品" else "milk"
```

**ProductRecord 部分修复正确。** 但 HQProductRecord 的 kind 仍硬编码为 `"milk"`（第 259-260 行、第 271-272 行），详见新发现问题 [P2-N6](#p2-n6-hqproductrecord-kind-仍硬编码为-milk)。

#### P2-27 修复验证: expand_selection 被调用两次 — 已修复

**位置**: 第 226-234 行

```python
if selection is not None:
    processed_files = expand_selection(repo_dir, selection)
    selected = set(processed_files)
    full = False
else:
    processed_files = [r for top in TOP_FOLDERS for r in _walk_top(repo_dir, top)]
    selected = None
    full = True
```

`expand_selection` 只调用一次，结果同时用于 `selected` 和 `processed_files`。**修复正确。**

#### P2-28 修复验证: 文件解析无错误处理 — 已修复

**位置**: 第 244-279 行

```python
try:
    if kind == "product_text" and ext == ".md":
        # ...
    else:
        # ...
except Exception as e:
    logger.error("处理文件失败 %s: %s: %s", rel, type(e).__name__, e)
    continue
```

每个文件的处理包裹在 try/except 中，失败时记录日志并继续处理其他文件。**修复正确。**

---

### 2.10 harness/test_admin_api.py

#### P1-10 修复验证: 未测试 Bearer Token 认证 — 已修复

**位置**: 第 334-347 行（`a19_bearer_token_auth`）

测试三个场景：无 token → 401、正确 token → 200、错误 token → 401。使用 `patch.dict` 隔离环境变量。**修复正确。**

#### P1-11 修复验证: 未测试跨租户越权 — 已修复

**位置**: 第 350-382 行（`a20_cross_tenant_isolation`）

测试插入 `ent_other` 的商品，以 `ent_test` 身份调用 `confirm_product` 和 `delete_product`，验证抛出 `PermissionError`。**修复正确。**

#### P2-18 修复验证: 测试修改全局环境变量 — 已修复

**位置**: 第 92 行、第 314 行

```python
# a3 测试
with patch.dict(os.environ, {"AGENT_CONFIG_PATH": yaml_path}):

# a16 测试
with patch.dict(os.environ, {}, clear=False):
    os.environ.pop("BUNDLE_INBOX_DIR", None)
```

使用 `mock.patch.dict` 隔离环境变量修改。**修复正确。**

---

### 2.11 harness/test_ppstructure_table.py

#### P2-19 修复验证: 测试不清理 classifier 全局缓存 — 已修复

**位置**: 第 151-154 行、第 180-183 行

```python
finally:
    cls_mod._overrides_cache = {}
    cls_mod._overrides_path = None
    cls_mod._overrides_mtime = 0.0
```

两处测试均在 `finally` 块中清理 classifier 缓存。**修复正确。**

---

### 2.12 harness/test_dataproc_pdf.py

#### P1-12 修复验证: 使用已废弃的 tempfile.mktemp — 已修复

**位置**: 第 50-54 行

```python
def _make_temp_pdf():
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    return path
```

改用 `tempfile.mkstemp`。**修复正确。**

#### P1-13 修复验证: 测试失败时临时文件未清理 — 已修复

**位置**: 第 69-78 行、第 81-93 行、第 97-115 行

所有测试块均使用 `try/finally` 确保 `os.unlink(p)` 执行：
```python
try:
    # ... 测试逻辑 ...
finally:
    if os.path.exists(p):
        os.unlink(p)
```

**修复正确。**

#### P2-21 修复验证: 测试辅助函数不关闭 fitz 文档 — 已修复

**位置**: 第 29-36 行、第 39-47 行

```python
def _digital_pdf(path, text):
    doc = fitz.open()
    try:
        # ...
        doc.save(path)
    finally:
        doc.close()  # P2-21: 确保关闭 fitz 文档
```

两个辅助函数均使用 `try/finally` 关闭 fitz 文档。**修复正确。**

---

## 3. 新发现问题

### P1 — 功能缺陷（5 项）

#### P1-N1: 跨租户操作遗漏 — delete_employee / unbind_gateway 不校验 enterprise_id

**位置**: src/admin/server.py 第 276-280 行、第 317-325 行

```python
# 第 276-280 行
@app.delete("/api/employees/{emp_id}", dependencies=[Depends(_verify_token)])
def delete_employee(emp_id: int):
    with db_tx(admin_db) as conn:
        conn.execute("DELETE FROM admin_employees WHERE id=?", (emp_id,))
    return {"status": "ok"}

# 第 317-325 行
@app.delete("/api/gateway/{emp_id}", dependencies=[Depends(_verify_token)])
def unbind_gateway(emp_id: int):
    with db_tx(admin_db) as conn:
        conn.execute(
            "UPDATE admin_employees SET bot_token=NULL, bound_at=NULL WHERE id=?",
            (emp_id,),
        )
    return {"status": "ok"}
```

**问题**: 上一轮修复了 `list_employees` 和 `list_gateway_bindings` 的跨租户读取问题（P0-01），但 `delete_employee` 和 `unbind_gateway` 仍按 `id` 操作，不校验记录是否属于当前 `cfg.enterprise_id`。由于 `create_employee`（第 266-274 行）和 `bind_gateway`（第 300-315 行）接受请求体中的任意 `enterprise_id`，`admin_employees` 表中可包含多企业数据。已认证 admin 可通过枚举 `emp_id` 删除其他企业的员工或解绑其他企业的网关。

同理，`create_employee` 和 `bind_gateway` 接受任意 `enterprise_id` 与 `list_employees`/`list_gateway_bindings` 仅返回当前企业数据的设计不一致。

**缓解因素**: 服务绑定 127.0.0.1 且需 Bearer Token 认证，攻击面有限。

**修复建议**: 在 DELETE/UPDATE 语句中加入 `enterprise_id` 条件：

```python
@app.delete("/api/employees/{emp_id}", dependencies=[Depends(_verify_token)])
def delete_employee(emp_id: int):
    with db_tx(admin_db) as conn:
        cur = conn.execute(
            "DELETE FROM admin_employees WHERE id=? AND enterprise_id=?",
            (emp_id, cfg.enterprise_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "员工不存在或无权操作")
    return {"status": "ok"}
```

---

#### P1-N2: 跨租户宝宝档案详情访问 — get_baby_detail 不校验 enterprise_id

**位置**: src/admin/server.py 第 352-373 行

```python
@app.get("/api/babies/{baby_id}", dependencies=[Depends(_verify_token)])
def get_baby_detail(baby_id: int):
    baby_store = _get_baby_store()
    b = baby_store.get_baby(baby_id)
    if b is None:
        raise HTTPException(404, f"宝宝档案不存在: {baby_id}")
    return {
        "baby_id": b.baby_id,
        "name": b.name,
        "budget": b.budget,
        "brand_preference": b.brand_preference,
        "birth_date": b.birth_date,
        "gestational_weeks": b.gestational_weeks,
        # ...
    }
```

**问题**: `list_babies`（P0-01 修复）正确使用 `cfg.enterprise_id` 过滤，但 `get_baby_detail` 直接按 `baby_id` 查询，不校验宝宝是否属于当前企业。已认证 admin 可通过枚举 `baby_id` 查看其他企业的宝宝详情，包括 `budget`、`brand_preference`、`birth_date`、`gestational_weeks` 等敏感字段。

**修复建议**: 在返回详情前校验 `enterprise_id`：

```python
@app.get("/api/babies/{baby_id}", dependencies=[Depends(_verify_token)])
def get_baby_detail(baby_id: int):
    baby_store = _get_baby_store()
    b = baby_store.get_baby(baby_id)
    if b is None:
        raise HTTPException(404, f"宝宝档案不存在: {baby_id}")
    if b.enterprise_id != cfg.enterprise_id:
        raise HTTPException(403, "无权访问该宝宝档案")
    return { ... }
```

---

#### P1-N3: PaddleOCR 单例在 pdf.py 和 image_table.py 中非线程安全

**位置**: tools/dataproc/adapters/pdf.py 第 12-29 行、tools/dataproc/adapters/image_table.py 第 19-36 行

```python
# pdf.py
_ocr_engine = None
_ocr_initialized = False

def _get_paddle_ocr():
    global _ocr_engine, _ocr_initialized
    if _ocr_initialized:
        return _ocr_engine
    _ocr_initialized = True  # ← 无锁保护，竞态窗口
    try:
        from paddleocr import PaddleOCR
        _ocr_engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    except Exception as e:
        ...
    return _ocr_engine
```

**问题**: `_ppstructure.py` 的 `get_ppstructure()`（P2-07 修复）正确使用了 `threading.Lock` + 双重检查锁定模式，但 `pdf.py` 和 `image_table.py` 中的 `_get_paddle_ocr()` 未使用任何锁。多线程同时调用时，两个线程都可能通过 `if _ocr_initialized` 检查，各自创建 PaddleOCR 实例。PaddleOCR 初始化会加载 ML 模型到内存，重复实例化导致：
1. 内存浪费（每个实例加载独立的模型权重）
2. GPU 内存耗尽风险（如果使用 GPU 推理）
3. 第一个实例被覆盖后成为内存泄漏

**修复建议**: 仿照 `_ppstructure.py` 的正确实现，添加 `threading.Lock` + 双重检查：

```python
import threading
_ocr_lock = threading.Lock()

def _get_paddle_ocr():
    global _ocr_engine, _ocr_initialized
    if _ocr_initialized:
        return _ocr_engine
    with _ocr_lock:
        if _ocr_initialized:
            return _ocr_engine
        _ocr_initialized = True
        try:
            from paddleocr import PaddleOCR
            _ocr_engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        except Exception as e:
            logger.warning("PaddleOCR 实例初始化失败: %s: %s", type(e).__name__, e)
            _ocr_engine = None
    return _ocr_engine
```

---

#### P1-N4: image_table.py _ocr_image 缺少防御性 OCR 解包

**位置**: tools/dataproc/adapters/image_table.py 第 101-102 行

```python
for line in _reading_order(res[0]):
    box, (txt, score) = line  # ← 无 try/except
    if score < 0.5:
        low_conf = True
    texts.append(txt)
```

**问题**: 上一轮修复了 `pdf.py` 的 OCR 解包防御（P1-08，添加了 `try/except (ValueError, TypeError)`），但 `image_table.py` 中的相同代码模式未同步修复。如果 PaddleOCR 返回结果格式因版本变化而不同，此处会抛 `ValueError`，导致整个图片 OCR 中断。

**修复建议**: 与 `pdf.py` 保持一致的防御性解包：

```python
for line in _reading_order(res[0]):
    try:
        box, (txt, score) = line
    except (ValueError, TypeError):
        logger.warning("跳过无法解析的 OCR 行: %r", line)
        continue
    if score < 0.5:
        low_conf = True
    texts.append(txt)
```

---

#### P1-N5: PermissionError / ValueError 未被 API 层捕获，导致 500 错误

**位置**: src/admin/server.py 第 208-228 行

```python
@app.post("/api/database/confirm", dependencies=[Depends(_verify_token)])
def db_confirm(product_id: int, value: str, table: str = "products_milk"):
    try:
        validate_table(table)
    except ValueError as e:
        raise HTTPException(400, str(e))
    store = _get_store()
    store.confirm_product(product_id, value, table, cfg.enterprise_id)  # ← 可能抛 PermissionError/ValueError
    return {"status": "ok"}
```

**问题**: `store.confirm_product` 和 `store.delete_product` 在跨租户时抛出 `PermissionError`，商品不存在时抛出 `ValueError`。这些异常未被 API 层捕获，FastAPI 默认返回 500 Internal Server Error。虽然 FastAPI 在非调试模式下不暴露异常详情（返回 `{"detail": "Internal Server Error"}`），但：
1. 客户端收到 500 而非语义正确的 403/404，无法区分"系统故障"和"权限拒绝"
2. 跨租户操作被正确拒绝但以 500 形式呈现，前端无法给出有意义提示
3. 服务器日志会记录为 ERROR 级别（实际应为 WARN/INFO 级别的安全事件）

**修复建议**: 在 API 层捕获 `PermissionError` 和 `ValueError`：

```python
@app.post("/api/database/confirm", dependencies=[Depends(_verify_token)])
def db_confirm(product_id: int, value: str, table: str = "products_milk"):
    try:
        validate_table(table)
    except ValueError as e:
        raise HTTPException(400, str(e))
    store = _get_store()
    try:
        store.confirm_product(product_id, value, table, cfg.enterprise_id)
    except PermissionError as e:
        logger.warning("跨租户操作被拒绝: %s", e)
        raise HTTPException(403, "无权操作该商品")
    except ValueError as e:
        raise HTTPException(404, str(e))
    logger.info("商品确认: id=%s table=%s ent=%s", product_id, table, cfg.enterprise_id)
    return {"status": "ok"}
```

---

### P2 — 代码质量（7 项）

#### P2-N1: init_admin_db 仍使用 connect() 而非 db_tx

**位置**: src/admin/models.py 第 33-37 行

```python
def init_admin_db(db_path: str):
    with connect(db_path) as conn:
        conn.executescript(ADMIN_SCHEMA)
        conn.commit()
```

**问题**: `init_admin_db` 是 P1-02/P1-14 连接泄露修复的遗漏点。虽然此函数仅在启动时调用一次，泄露影响有限，但与项目统一使用 `db_tx` 的策略不一致。此外 `db_tx` 已自动处理 commit/rollback，显式 `conn.commit()` 是冗余的。

**修复建议**: 改为 `with db_tx(db_path) as conn:`，移除显式 `conn.commit()`。

---

#### P2-N2: StoreCreate.db_path 仍缺少路径校验

**位置**: src/admin/models.py 第 57-60 行

```python
class StoreCreate(BaseModel):
    enterprise_id: str = Field(min_length=1)
    enterprise_name: str = Field(min_length=1)
    db_path: str = "instance.db"  # ← 无路径校验
```

**问题**: 上一轮 P2-04 指出 `db_path` 字段接受任意字符串（如 `../../etc/malicious.db`），可被用于路径遍历。修复仅添加了 `min_length=1` 约束到 `enterprise_id` 和 `enterprise_name`，但 `db_path` 的路径校验未实现。虽然 `db_path` 当前仅存储到 `admin_stores` 表（不立即用于创建文件），但后续使用该路径创建 `KnowledgeStore` 时可能覆盖系统文件。

**修复建议**: 添加 `field_validator` 校验 `db_path`：

```python
from pydantic import field_validator

class StoreCreate(BaseModel):
    enterprise_id: str = Field(min_length=1, max_length=64)
    enterprise_name: str = Field(min_length=1, max_length=128)
    db_path: str = "instance.db"

    @field_validator("db_path")
    @classmethod
    def validate_db_path(cls, v: str) -> str:
        if ".." in v:
            raise ValueError("db_path 不允许包含 ..")
        return v
```

---

#### P2-N3: _get_paddle_ocr() 在 pdf.py 和 image_table.py 中重复定义

**位置**: tools/dataproc/adapters/pdf.py 第 12-29 行、tools/dataproc/adapters/image_table.py 第 19-36 行

**问题**: 完全相同的 `_get_paddle_ocr()` 函数和模块级状态（`_ocr_engine`, `_ocr_initialized`）在两个文件中重复定义。项目已通过提取 `_ppstructure.py` 消除了 PP-Structure 的代码重复，但 PaddleOCR 单例的重复未同步处理。这导致两份独立的 PaddleOCR 实例缓存，同进程中可能同时存在两个 PaddleOCR 实例（浪费内存）。

**修复建议**: 提取到 `_paddle_ocr.py` 共享模块，或在 `_ppstructure.py` / `adapters/__init__.py` 中统一管理：

```python
# tools/dataproc/adapters/_paddle_ocr.py
import threading, logging
logger = logging.getLogger(__name__)
_ocr_engine = None
_ocr_initialized = False
_ocr_lock = threading.Lock()

def get_paddle_ocr():
    global _ocr_engine, _ocr_initialized
    if _ocr_initialized:
        return _ocr_engine
    with _ocr_lock:
        if _ocr_initialized:
            return _ocr_engine
        _ocr_initialized = True
        try:
            from paddleocr import PaddleOCR
            _ocr_engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        except Exception as e:
            logger.warning("PaddleOCR 初始化失败: %s: %s", type(e).__name__, e)
            _ocr_engine = None
    return _ocr_engine
```

---

#### P2-N4: API Key 仍以明文存储在 YAML 文件中

**位置**: src/admin/server.py 第 166-167 行

**问题**: 上一轮 P1-05 的修复建议包含两点：(1) API Key 应存储在环境变量或加密存储中；(2) 空提交不覆盖已有 key。第 (2) 点已修复（`if update.api_key:`），但第 (1) 点未实现。API Key 仍以明文写入 YAML 配置文件。若该文件权限不当或被版本控制纳入，API Key 将泄露。

**修复建议**: 将 API Key 存储在环境变量中（如 `AGENT_LLM_API_KEY`），YAML 中仅保存 `<from-env>` 占位符，运行时从环境变量读取。

---

#### P2-N5: colspan/rowspan 功能缺少测试覆盖

**位置**: harness/test_ppstructure_table.py 第 53-61 行

**问题**: 上一轮 P2-08 修复了 `TableHTMLParser` 的 colspan/rowspan 支持（完全重写解析器），但测试 `t3_html_parser_from_shared` 仅测试简单 2x2 表格，未覆盖 colspan/rowspan 场景。新增的复杂逻辑（`_occupied` 集合、跨度填充、列跳过）缺乏测试验证。

**修复建议**: 添加 colspan/rowspan 测试用例：

```python
def t3b_html_parser_colspan_rowspan():
    """T3b: TableHTMLParser 支持 colspan/rowspan。"""
    from dataproc.adapters._ppstructure import TableHTMLParser
    html = '''<table>
    <tr><td colspan="2">合并单元格</td></tr>
    <tr><td>A</td><td>B</td></tr>
    </table>'''
    parser = TableHTMLParser()
    parser.feed(html)
    assert len(parser.rows) == 2
    assert parser.rows[0] == ["合并单元格", "合并单元格"]
    assert parser.rows[1] == ["A", "B"]
```

---

#### P2-N6: HQProductRecord kind 仍硬编码为 "milk"

**位置**: tools/dataproc/build.py 第 259-260 行、第 271-272 行

```python
# 第 259-260 行
hq_products.append(HQProductRecord(
    kind="milk", fields=fields, meta={"vendor": ent_id}).to_dict())

# 第 271-272 行
hq_products.append(HQProductRecord(
    kind="milk", fields=product_dict["fields"],
    meta={"vendor": ent_id}).to_dict())
```

**问题**: 上一轮 P1-16 修复了 `ProductRecord` 的 kind 硬编码（现在根据 `classify()` 推断），但 `HQProductRecord` 的 kind 仍硬编码为 `"milk"`。营养品类型的 HQ 商品也会被标记为 `"milk"`，后续播种到实例时可能被错误地插入 `products_milk` 表。

**修复建议**: 与 ProductRecord 一致，根据分类结果推断 kind：

```python
# 第 259-260 行（build_bundle 中 .md 产品路径）
hq_products.append(HQProductRecord(
    kind=prod_kind, fields=fields, meta={"vendor": ent_id}).to_dict())

# 第 271-272 行（_process_nontext 路径）
hq_products.append(HQProductRecord(
    kind=prod_kind, fields=product_dict["fields"],
    meta={"vendor": ent_id}).to_dict())
```

---

#### P2-N7: delete_product Chroma 删除失败静默忽略，导致数据不一致

**位置**: src/kb/store.py 第 264-269 行

```python
# P2-24: 批量删除 Chroma 向量
if ids:
    try:
        self.collection.delete(ids=[str(i) for i in ids])
    except Exception:
        pass  # ← 静默忽略
```

**问题**: SQLite 中的 corpus 记录和 FTS 索引已在事务内删除并提交，但 Chroma 向量删除在事务外执行，且失败时被 `except Exception: pass` 静默忽略。如果 Chroma 删除失败（如网络问题、Chroma 内部错误），向量数据将残留在 Chroma 中成为孤儿数据。后续检索时这些孤儿向量仍可被召回，但回查 corpus 时找不到对应记录会被跳过，造成检索效率下降和潜在的隐私问题（跨企业向量残留）。

**修复建议**: 至少记录警告日志，并考虑重试机制：

```python
if ids:
    try:
        self.collection.delete(ids=[str(i) for i in ids])
    except Exception as e:
        logger.warning("Chroma 向量删除失败（corpus ids=%s）: %s: %s", ids, type(e).__name__, e)
```

---

### P3 — 建议优化（5 项）

#### P3-N1: HTML 页面无需认证即可访问

**位置**: src/admin/server.py 第 114-137 行

**问题**: 所有 `/admin/*` 页面路由均无 `Depends(_verify_token)`。页面 HTML 中直接嵌入 `cfg.enterprise_id`、`cfg.llm.kind`、`cfg.llm.model`、`cfg.llm.base_url` 等信息。虽然 API 端点需认证，但页面本身可被未认证访问者查看，泄露实例配置信息。由于服务绑定 127.0.0.1，风险较低。

**修复建议**: 为页面路由也添加认证，或在页面中不直接嵌入敏感配置，改为通过认证 API 获取。

---

#### P3-N2: scanInbox 前端未处理 API 错误响应

**位置**: src/admin/pages.py 第 214-221 行

```javascript
async function scanInbox() {{
  const r = await fetch('/api/database/scan', {{method:'POST'}});
  const d = await r.json();
  let html = `<div class="card" style="background:#d1fae5;">扫描完成</div>`;  // ← 即使失败也显示"扫描完成"
  // ...
}}
```

**问题**: 当 API 返回 400（如"收件箱目录未配置"）时，`d` 为 `{"detail": "..."}`，`d.loaded`/`d.failed` 为 `undefined`。函数仍显示"扫描完成"成功消息，误导用户。

**修复建议**: 检查 `r.ok` 并区分成功/失败：

```javascript
async function scanInbox() {{
  const r = await fetch('/api/database/scan', {{method:'POST'}});
  const d = await r.json();
  if (!r.ok) {{
    document.getElementById('scan-result').innerHTML =
      `<div class="card" style="background:#fee2e2;">扫描失败: ${{esc(d.detail || '未知错误')}}</div>`;
    return;
  }}
  // ... 成功逻辑 ...
}}
```

---

#### P3-N3: confirmProduct / deleteProduct 前端未处理 API 错误

**位置**: src/admin/pages.py 第 223-231 行

**问题**: `confirmProduct` 和 `deleteProduct` 函数直接 `await fetch(...)` 后调用 `loadPending()`，不检查响应状态。如果 API 返回 403（跨租户拒绝）或 404（商品不存在），用户无任何反馈。

**修复建议**: 检查 `r.ok` 并提示错误信息。

---

#### P3-N4: db_tx 块内冗余的 conn.commit() 调用

**位置**: src/kb/store.py 多处（第 142、157、173、232、263、282、300、328、347、375、412、459 行等）

**问题**: `db_tx` 上下文管理器在 `yield` 后已自动调用 `conn.commit()`（见 `common/db.py` 第 29 行）。但 store.py 中多处方法在 `with db_tx()` 块内显式调用 `conn.commit()`，这是冗余的。虽然不会导致错误（SQLite 的重复 commit 是 no-op），但：
1. 代码意图不清晰（读者可能误以为 `db_tx` 不自动提交）
2. 如果在显式 commit 之后发生异常，`db_tx` 的 `conn.rollback()` 无法回滚已提交的事务

**修复建议**: 移除 `db_tx` 块内的显式 `conn.commit()` 调用，依赖 `db_tx` 的自动提交机制。

---

#### P3-N5: _get_baby_store 每次调用创建新实例

**位置**: src/admin/server.py 第 111-112 行

```python
def _get_baby_store() -> BabyProfileStore:
    return BabyProfileStore(cfg.baby_db_path or cfg.db_path)
```

**问题**: 与 `_get_store()` 使用线程安全单例不同，`_get_baby_store()` 每次调用都创建新的 `BabyProfileStore` 实例。`BabyProfileStore.__init__` 调用 `_init_schema()`，每次都执行 `CREATE TABLE IF NOT EXISTS` 和 `ALTER TABLE` 语句。每次 `/api/babies` 请求都触发一次 schema 初始化，虽然幂等但浪费资源。

**修复建议**: 仿照 `_get_store()` 使用单例模式：

```python
_baby_holder: dict = {"store": None}
_baby_lock = threading.Lock()

def _get_baby_store() -> BabyProfileStore:
    if _baby_holder["store"] is None:
        with _baby_lock:
            if _baby_holder["store"] is None:
                _baby_holder["store"] = BabyProfileStore(cfg.baby_db_path or cfg.db_path)
    return _baby_holder["store"]
```

---

## 4. 问题汇总

### 新发现 P1 — 功能缺陷（5 项）

| 编号 | 文件 | 行号 | 问题 | 上轮关联 |
|------|------|------|------|----------|
| P1-N1 | server.py | 276-280, 317-325 | delete_employee / unbind_gateway 不校验 enterprise_id（跨租户操作遗漏） | P0-01 修复不完整 |
| P1-N2 | server.py | 352-373 | get_baby_detail 不校验 enterprise_id（跨租户宝宝详情访问） | P0-01 修复不完整 |
| P1-N3 | pdf.py, image_table.py | 12-29, 19-36 | PaddleOCR 单例非线程安全（缺少 threading.Lock） | P2-09/P2-13 修复引入 |
| P1-N4 | image_table.py | 101-102 | _ocr_image 缺少防御性 OCR 解包 | P1-08 修复未同步 |
| P1-N5 | server.py | 208-228 | PermissionError/ValueError 未被 API 捕获，导致 500 | P0-02 修复的配套遗漏 |

### 新发现 P2 — 代码质量（7 项）

| 编号 | 文件 | 行号 | 问题 |
|------|------|------|------|
| P2-N1 | models.py | 33-37 | init_admin_db 仍使用 connect() 而非 db_tx |
| P2-N2 | models.py | 57-60 | StoreCreate.db_path 缺少路径校验 |
| P2-N3 | pdf.py, image_table.py | 12-29, 19-36 | _get_paddle_ocr() 重复定义 |
| P2-N4 | server.py | 166-167 | API Key 仍明文存储在 YAML |
| P2-N5 | test_ppstructure_table.py | 53-61 | colspan/rowspan 功能缺少测试 |
| P2-N6 | build.py | 259-260, 271-272 | HQProductRecord kind 硬编码为 "milk" |
| P2-N7 | store.py | 264-269 | Chroma 删除失败静默忽略，数据不一致 |

### 新发现 P3 — 建议优化（5 项）

| 编号 | 文件 | 行号 | 问题 |
|------|------|------|------|
| P3-N1 | server.py | 114-137 | HTML 页面无需认证即可访问 |
| P3-N2 | pages.py | 214-221 | scanInbox 前端未处理 API 错误 |
| P3-N3 | pages.py | 223-231 | confirmProduct/deleteProduct 前端未处理 API 错误 |
| P3-N4 | store.py | 多处 | db_tx 块内冗余 conn.commit() |
| P3-N5 | server.py | 111-112 | _get_baby_store 每次创建新实例 |

### 上轮未修复问题（P2 级，低优先级）

| 编号 | 文件 | 问题 |
|------|------|------|
| P2-20 | test_ppstructure_table.py | 通过子进程运行其他测试 |
| P2-23 | store.py | retrieve 方法过长（145 行） |
| P2-26 | build.py | build_bundle 函数过长 |

---

## 5. 总体评分与总结

### 总体评分: 4.0 / 5.0

（上轮评分 3.0 / 5.0，本轮提升 1.0 分）

### 评分依据

| 维度 | 上轮评分 | 本轮评分 | 说明 |
|------|----------|----------|------|
| 安全性 | 2.5 | 4.0 | 全部 4 个 P0 已正确修复；跨租户校验核心路径已覆盖；但 delete_employee/unbind_gateway/get_baby_detail 三处遗漏（P1-N1/N2），且 PermissionError 未正确处理 |
| 功能正确性 | 3.0 | 4.0 | kind 推断、连接管理、资源释放均修复；但 PaddleOCR 线程安全新问题和 image_table 解包遗漏影响稳定性 |
| 代码质量 | 3.5 | 4.0 | esc() 统一、正则预编译、单例模式、per-file 异常捕获等大幅改善；残留代码重复和冗余提交 |
| 测试覆盖 | 3.0 | 4.0 | 新增 A19（认证）和 A20（跨租户）测试；但 colspan/rowspan 新功能缺测试 |
| 可维护性 | 3.0 | 4.0 | 日志体系建立、公共模块提取、线程安全改进显著 |

### 修复质量评价

**优点**:
1. P0 修复质量高: 全部 4 个安全漏洞均正确、完整修复，修复方式符合最佳实践（`secrets.compare_digest`、双重检查锁定、`db_tx` 替换、`textContent` + `createElement`）。
2. P1 核心修复到位: 时序攻击、连接泄露（20+ 处统一替换）、线程安全、资源泄露（fitz/PIL/文件句柄）、kind 推断等关键问题均正确修复。
3. 安全测试补充: A19（Bearer Token 三场景）和 A20（跨租户 PermissionError 验证）填补了上轮的安全测试盲区。
4. 代码质量提升: `_BASE_JS` 公共提取、正则预编译、`threading.Lock` 保护、per-file 异常捕获等改进显著提升了可维护性。
5. `_slice_long` 修复经验证正确: 通过 `last_y` 跟踪机制避免了末尾切片重复，多组参数验证通过。
6. `TableHTMLParser` colspan/rowspan 实现完整: `_occupied` 集合跟踪 + `_int_attr` 防御性解析，设计合理。

**需改进**:
1. 跨租户修复不完整: P0-01 修复了 list 端点但遗漏了 delete/unbind/get_detail 端点，导致安全防护存在缺口。建议对所有按 `id` 操作的端点统一审计。
2. PaddleOCR 单例修复引入新问题: `pdf.py` 和 `image_table.py` 的单例未同步 `_ppstructure.py` 的 Lock 模式，属于修复不一致。
3. 异常处理不完整: `PermissionError` 在 API 层未被捕获，跨租户拒绝以 500 呈现，前端无法区分错误类型。
4. 测试覆盖不足: colspan/rowspan 这一重大功能重写缺乏对应测试。
5. 部分修复不完整: `db_path` 路径校验、API Key 明文存储、HQ kind 硬编码等问题仅部分修复或遗漏。

### 修复优先级建议

1. **第一优先**（上线前必须修复）: P1-N1, P1-N2（跨租户遗漏）, P1-N5（API 异常处理）
2. **第二优先**（高优先）: P1-N3（PaddleOCR 线程安全）, P1-N4（image_table 解包防御）
3. **第三优先**: P2-N1, P2-N2, P2-N6, P2-N7
4. **后续迭代**: P2-N3, P2-N4, P2-N5, P3 全部

---

*报告生成时间: 2026-07-22*  
*审查文件数: 12（+ 4 支撑文件）*  
*上轮问题验证: P0=4/4 修复, P1=9/12 完全修复 + 3 部分修复, P2=13/18 完全修复 + 3 部分修复 + 2 未修复*  
*新发现问题: P0=0, P1=5, P2=7, P3=5*
