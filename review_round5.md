# 第五轮安全审查报告

## 审查概要
- 审查时间: 2026-07-22
- 审查范围: 24 个文件（核心源码 14 + 数据处理工具 6 + 测试 3 + 安全脚本 1）
- 总体评分: 4.8 / 5.0
- 上轮评分: 4.5 / 5.0（本轮提升 0.3 分）

## 问题汇总
| 级别 | 数量 |
|------|------|
| P0 (致命) | 0 |
| P1 (严重) | 0 |
| P2 (中等) | 2 |
| P3 (建议) | 11 |

### 评分依据
- P0=0, P1=0, P2=2 → 5.0 - 0 x 1.0 - 0 x 0.3 - 2 x 0.1 = 4.8

### 第四轮修复验证总览

| 上轮编号 | 级别 | 问题 | 修复状态 |
|----------|------|------|----------|
| P2-R4-1 | P2 | delete_corpus/update_corpus 缺少 enterprise_id 跨租户校验 | 已修复 |
| P2-R4-2 | P2 | delete_corpus Chroma 删除仍静默忽略异常 | 已修复 |
| P2-R4-3 | P2 | list_stores 返回所有门店数据，无 enterprise_id 过滤 | 已修复 |
| P2-R4-4 | P2 | create_employee/bind_gateway 仍接受任意 enterprise_id | 已修复 |
| P2-R4-5 | P2 | T3b 测试仅覆盖 colspan，未覆盖 rowspan | 已修复 |
| P2-R4-6 | P2 | 缺少跨租户拒绝路径的 API 级测试 | 部分修复 |

**结论**: 第四轮发现的 6 个 P2 问题中，5 个已完全修复，1 个部分修复（A21 仅覆盖 confirm/delete 商品跨租户拒绝，其余跨租户拒绝路径仍未测试）。修复质量良好，未引入 P0/P1 级别的新问题。

---

## 详细问题列表

### P0 级别（致命 — 必须立即修复）

无。

### P1 级别（严重 — 本轮必须修复）

无。

### P2 级别（中等 — 本轮必须修复）

#### P2-R5-1: baby/store.py 全部方法存在 SQLite 连接泄漏

**位置**: src/baby/store.py 全部 14 个数据库方法（第 63、115、132、149、163、174、194、230、251、269、295、323、336、362 行）

**问题**: `BabyProfileStore` 的所有方法均使用 `with connect(self.db_path) as conn:` 模式操作数据库。然而 `connect()` 返回的是原生 `sqlite3.Connection` 对象，其上下文管理器 `__exit__` 方法仅处理事务提交/回滚，**不会关闭连接**（这是 Python sqlite3 模块的文档化行为）。对比 `common/db.py` 中的 `db_tx` 上下文管理器，后者在 `finally` 块中显式调用 `conn.close()`。

```python
# baby/store.py — 泄漏连接的模式
from common.db import connect  # 导入的是 connect，不是 db_tx

def get_baby(self, baby_id: int) -> Optional[BabyProfile]:
    with connect(self.db_path) as conn:  # __exit__ 不关闭连接!
        row = conn.execute(
            "SELECT * FROM babies WHERE baby_id=?", (baby_id,)
        ).fetchone()
    # conn 在此处未被关闭，等待 GC 回收
```

```python
# common/db.py — 正确的模式
@contextmanager
def db_tx(db_path: str | Path):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()  # 显式关闭
```

**影响**:
1. 每次 API 调用 `/api/babies` 或 `/api/babies/{id}` 时，`_get_baby_store()` 创建新的 `BabyProfileStore` 实例（P3-R4-5 未修复），`_init_schema()` 泄漏 1 个连接，实际业务方法再泄漏 1 个连接
2. 长时间运行的服务端进程会持续累积未关闭的 SQLite 连接，可能导致文件描述符耗尽
3. 并发场景下可能出现 "database is locked" 错误，影响服务可用性

**修复建议**: 将所有 `with connect(self.db_path) as conn:` 替换为 `with db_tx(self.db_path) as conn:`，并移除冗余的 `conn.commit()` 调用（`db_tx` 自动提交）：

```python
from common.db import db_tx  # 改为导入 db_tx

def get_baby(self, baby_id: int) -> Optional[BabyProfile]:
    with db_tx(self.db_path) as conn:
        row = conn.execute(
            "SELECT * FROM babies WHERE baby_id=?", (baby_id,)
        ).fetchone()
    # db_tx 在 finally 中关闭连接
```

---

#### P2-R5-2: 跨租户拒绝路径的 API 级测试覆盖不完整

**位置**: harness/test_admin_api.py

**问题**: 第四轮 P2-R4-6 要求补充 5 类跨租户拒绝路径的 API 级测试。当前 A21 仅覆盖了 confirm/delete 商品的跨租户拒绝（返回 403），以下场景仍无 API 级测试：

| 场景 | 预期响应 | 测试状态 |
|------|----------|----------|
| 跨租户 confirm 商品 → 403 | 403 | A21 已覆盖 |
| 跨租户 delete 商品 → 403 | 403 | A21 已覆盖 |
| 跨租户 delete_employee → 404 | 404 | 未测试 |
| 跨租户 unbind_gateway → 404 | 404 | 未测试 |
| 跨租户 get_baby_detail → 403 | 403 | 未测试 |
| 跨租户 list_stores 不返回他企门店 | 空列表 | 未测试 |

当前跨租户安全校验代码已到位（本轮验证通过），但缺乏 API 级拒绝路径测试意味着未来修改可能意外移除安全校验而无法被测试发现。

**修复建议**: 补充以下测试用例：

```python
def a22_cross_tenant_delete_employee_404():
    """A22: 跨租户删除员工 → 404。"""
    db = _tmp_db()
    client, _ = _make_client(db)
    # 直接插入他企员工
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO admin_employees(enterprise_id, employee_id, employee_name) "
            "VALUES(?,?,?)",
            ("ent_other", "emp_x", "他企员工"),
        )
        emp_id = cur.lastrowid
        conn.commit()
    # 当前实例 ent_test 尝试删除 → 404
    r = client.delete(f"/api/employees/{emp_id}")
    assert r.status_code == 404

def a23_cross_tenant_baby_detail_403():
    """A23: 跨租户查看宝宝详情 → 403。"""
    db = _tmp_db()
    client, _ = _make_client(db)
    from baby.store import BabyProfileStore
    from baby.models import BabyProfile
    store = BabyProfileStore(db)
    cid = store.get_or_create_customer("ent_other", "emp_x", "他企客户")
    bid = store.create_baby(BabyProfile(
        baby_id=None, enterprise_id="ent_other", employee_id="emp_x",
        customer_id=cid, name="他企宝宝",
    ))
    r = client.get(f"/api/babies/{bid}")
    assert r.status_code == 403
```

---

### P3 级别（建议 — 可选优化）

#### P3-R5-1: pages.py esc() 在 JavaScript 属性上下文中不足以防 XSS

**位置**: src/admin/pages.py 第 208-209 行

**问题**: `render_database_page` 中的 JavaScript `esc()` 函数使用 HTML 实体编码（`'` → `&#39;`），但在 `onclick` 属性内的 JavaScript 字符串上下文中，HTML 解析器会先将 `&#39;` 解码回 `'`，再交给 JavaScript 引擎执行。因此如果 `safeTable` 包含单引号，理论上可构造 XSS：

```
输入: p.table = "x'); alert('xss"
esc() 后: x&#39;); alert(&#39;xss
HTML 属性: onclick="confirmProduct(1,'x&#39;); alert(&#39;xss')"
HTML 解码后 JS: confirmProduct(1,'x'); alert('xss')  ← XSS!
```

**缓解因素**: `p.table` 来自 `store.list_pending_products()` 中的硬编码字典 `_PENDING_COL`（仅可为 `"products_milk"` 或 `"products_nutrition"`），不受用户输入控制，当前不可利用。

**修复建议**: 在 JavaScript 字符串上下文中，应使用 JavaScript 字符串转义（`'` → `\'`）而非仅 HTML 实体编码，或改用 `data-*` 属性 + `addEventListener` 绑定事件：

```javascript
// 方案一：添加 JS 字符串转义
function jsEsc(s) {
    return String(s||'').replace(/\\/g,'\\\\').replace(/'/g,"\\'")
        .replace(/"/g,'\\"').replace(/\n/g,'\\n').replace(/\r/g,'\\r');
}
// 使用: onclick="confirmProduct(${p.id},'${jsEsc(p.table)}')"
```

---

#### P3-R5-2: mask_token 对极短 token 不脱敏

**位置**: src/admin/models.py 第 90-99 行

**问题**: `mask_token` 对长度 1-2 的 token 完全不脱敏：

```python
def mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 12:
        return token[:2] + "*" * (len(token) - 2)
    return token[:4] + "*" * (len(token) - 8) + token[-4:]
```

- 长度 1: `token[:2]` 返回整个字符，`"*" * (1-2)` = `""` → 返回明文
- 长度 2: `token[:2]` 返回两个字符，`"*" * 0` = `""` → 返回明文

`GatewayBinding.bot_token` 的 Pydantic 验证为 `min_length=1`，理论上允许 1-2 字符的 token。虽然实际 iLink Bot Token 远长于此，但防御性编程应覆盖边界情况。

**修复建议**:

```python
def mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 4:
        return "*" * len(token)
    if len(token) <= 12:
        return token[:2] + "*" * (len(token) - 2)
    return token[:4] + "*" * (len(token) - 8) + token[-4:]
```

---

#### P3-R5-3: Pydantic 模型保留被忽略的 enterprise_id 字段

**位置**: src/admin/models.py 第 56-67 行（StoreCreate）、第 70-73 行（EmployeeCreate）、第 76-80 行（GatewayBinding）

**问题**: 第四轮 P2-R4-4 修复后，`create_store`、`create_employee`、`bind_gateway` 均强制使用 `cfg.enterprise_id`，忽略请求体中的 `enterprise_id`。但对应的 Pydantic 模型仍保留 `enterprise_id` 字段且为必填（`min_length=1`），导致：
1. API 契约误导：客户端必须传 `enterprise_id` 但服务端忽略它
2. 前端表单仍展示企业 ID 输入框（如 `render_stores_page` 的 `s-eid` 输入框），用户输入被静默丢弃

**修复建议**: 将 `enterprise_id` 从请求模型中移除，或标记为 `Optional` 并添加 `deprecated` 注释：

```python
class EmployeeCreate(BaseModel):
    employee_id: str = Field(min_length=1)
    employee_name: str = Field(min_length=1)
    # enterprise_id 已由服务端强制使用 cfg.enterprise_id，不再接受客户端传入
```

---

#### P3-R5-4: _ppstructure.py reset() 未使用锁（P3-R4-1 遗留）

**位置**: tools/dataproc/adapters/_ppstructure.py 第 161-165 行

**问题**: `_paddle_ocr.py` 的 `reset()` 正确使用 `_ocr_lock` 保护，但 `_ppstructure.py` 的 `reset()` 直接修改全局变量，未获取 `_pp_lock`。并行测试时可能导致竞态条件。

**修复建议**:

```python
def reset():
    global _pp_engine, _pp_initialized
    with _pp_lock:
        _pp_engine = None
        _pp_initialized = False
```

---

#### P3-R5-5: get_baby_detail 跨租户返回 403 存在信息泄露（P3-R4-2 遗留）

**位置**: src/admin/server.py 第 383-392 行

**问题**: 跨租户访问返回 403，不存在返回 404。已认证的攻击者可通过枚举 `baby_id`（自增整数）区分"宝宝存在于其他企业"（403）和"宝宝不存在"（404），推断其他企业的宝宝档案数量范围。

**修复建议**: 统一返回 404，不区分"不存在"和"无权限"：

```python
if b is None or b.enterprise_id != cfg.enterprise_id:
    if b is not None and b.enterprise_id != cfg.enterprise_id:
        logger.warning("跨租户宝宝档案访问被拒绝: baby_id=%s ent=%s", baby_id, cfg.enterprise_id)
    raise HTTPException(404, f"宝宝档案不存在: {baby_id}")
```

---

#### P3-R5-6: db_path 验证仅检查 ".."，未检查绝对路径（P3-R4-3 遗留）

**位置**: src/admin/models.py 第 61-67 行

**问题**: `StoreCreate.validate_db_path` 仅检查 `..`，接受绝对路径如 `/etc/evil.db` 或 `C:\Windows\System32\evil.db`。虽然 `db_path` 当前仅存储到 `admin_stores` 表元数据中，但后续使用该路径创建 `KnowledgeStore` 时可能覆盖系统文件。

**修复建议**:

```python
@field_validator("db_path")
@classmethod
def validate_db_path(cls, v: str) -> str:
    if ".." in v:
        raise ValueError("db_path 不允许包含 ..")
    if os.path.isabs(v):
        raise ValueError("db_path 必须为相对路径")
    return v
```

---

#### P3-R5-7: scan_and_load 未被 try/except 包裹（P3-R4-4 遗留）

**位置**: src/admin/server.py 第 195-203 行

**问题**: `scan_and_load` 可能因 bundle 文件损坏、磁盘 I/O 错误、JSON 解析失败等原因抛出异常，API 层未捕获，FastAPI 默认返回 500。与 `db_confirm`/`db_delete` 的异常处理策略不一致。

**修复建议**:

```python
@app.post("/api/database/scan", dependencies=[Depends(_verify_token)])
def db_scan():
    inbox = os.environ.get("BUNDLE_INBOX_DIR", "")
    if not inbox or not os.path.isdir(inbox):
        raise HTTPException(400, f"收件箱目录未配置或不存在: {inbox}")
    store = _get_store()
    try:
        result = scan_and_load(inbox, store, cfg.enterprise_id)
    except Exception as e:
        logger.error("bundle 扫描失败: %s: %s", type(e).__name__, e)
        raise HTTPException(500, f"扫描失败: {type(e).__name__}")
    logger.info("bundle 扫描完成: enterprise_id=%s", cfg.enterprise_id)
    return result
```

---

#### P3-R5-8: _get_baby_store() 每次调用创建新实例（P3-R4-5 遗留）

**位置**: src/admin/server.py 第 111-112 行

**问题**: 与 `_get_store()` 使用线程安全单例不同，`_get_baby_store()` 每次调用都创建新的 `BabyProfileStore` 实例，每次触发 `_init_schema()`。每次 `/api/babies` 请求都执行一次 schema 初始化，浪费资源。

**修复建议**: 仿照 `_get_store()` 使用单例模式。

---

#### P3-R5-9: 前端 API 调用未检查响应状态（P3-N2/N3 遗留）

**位置**: src/admin/pages.py 第 214-221 行（scanInbox）、第 223-231 行（confirmProduct/deleteProduct）

**问题**: 多处前端 `fetch` 调用未检查 `r.ok`，API 失败时仍显示"扫描完成"等成功信息，无错误反馈。

---

#### P3-R5-10: db_tx 块内冗余的 conn.commit() 调用（P3-N4 遗留）

**位置**: src/kb/store.py 多处（第 145、235、266、304、331 行等）

**问题**: `db_tx` 上下文管理器在 `yield` 后自动调用 `conn.commit()`，方法内部显式调用 `conn.commit()` 是冗余的。不会导致错误，但增加代码噪音。

---

#### P3-R5-11: pages.py 中 p.id 未转义直接插入 HTML

**位置**: src/admin/pages.py 第 206 行

**问题**: `render_database_page` 的 `loadPending` 函数中，`p.id` 直接插入 HTML 模板字符串（`${{p.id}}`），未经 `esc()` 转义。虽然 `p.id` 来自 SQLite `INTEGER PRIMARY KEY`，始终为整数，但与同区域 `safeName`、`safeBrand`、`safeTable` 均经过 `esc()` 转义的做法不一致。

**修复建议**: 为保持一致性，对 `p.id` 也进行转义或显式转换为数字：`Number(p.id)` 或 `esc(p.id)`。

---

## 各文件审查详情

### 1. src/admin/server.py

**认证与授权**: 所有 API 端点均配置 `dependencies=[Depends(_verify_token)]`，Bearer Token 认证使用 `secrets.compare_digest` 常量时间比较，防时序攻击。HTML 页面端点（`/`、`/admin/*`）无需认证（仅渲染静态 HTML，数据通过认证 API 获取），设计合理。

**跨租户隔离（第四轮修复验证）**:
- `list_stores`: 已添加 `WHERE enterprise_id=?` 过滤 ✓ (P2-R4-3 修复)
- `create_store`: 强制使用 `cfg.enterprise_id` ✓
- `create_employee`: 强制使用 `cfg.enterprise_id`，忽略请求体 ✓ (P2-R4-4 修复)
- `bind_gateway`: 强制使用 `cfg.enterprise_id`，忽略请求体 ✓ (P2-R4-4 修复)
- `delete_employee`: `WHERE id=? AND enterprise_id=?` ✓
- `unbind_gateway`: `WHERE id=? AND enterprise_id=?` ✓
- `list_employees`/`list_gateway_bindings`/`list_babies`: 均 `WHERE enterprise_id=?` ✓
- `get_baby_detail`: 检查 `b.enterprise_id != cfg.enterprise_id` ✓
- `db_confirm`/`db_delete`: 传入 `cfg.enterprise_id` 给 store 层校验 ✓
- `db_scan`/`db_pending`: 传入 `cfg.enterprise_id` ✓

**注入攻击**: 所有 SQL 查询使用参数化 `?` 占位符。表名通过 `validate_table` 白名单校验。YAML 路径来自环境变量（非 API 输入），检查 `..` 和后缀。

**数据泄露**: `list_employees`/`list_gateway_bindings` 返回的 `bot_token` 经 `mask_token` 脱敏。`list_babies`/`get_baby_detail` 不返回 `allergens`/`medical_history`/`feeding_history`/`health_notes` 等敏感字段。`get_llm_config` 的 `api_key` 仅返回 `<set>` 或空串。日志中不记录敏感值。

**资源管理**: `_get_store()` 使用双重检查锁定单例模式，线程安全。`_get_baby_store()` 每次创建新实例（P3-R5-8）。

**结论**: 无 P0/P1 问题。P2-R4-3/P2-R4-4 修复正确完整。

---

### 2. src/admin/models.py

**认证与授权**: N/A（数据模型层）。

**注入攻击**: `ALLOWED_TABLES` 白名单防 SQL 注入。`StoreCreate.validate_db_path` 检查 `..`（未检查绝对路径，P3-R5-6）。

**数据泄露**: `mask_token` 对短 token 不脱敏（P3-R5-2）。

**输入验证**: `LLMConfigUpdate` 有 `kind` 白名单验证、`temperature`/`max_tokens` 边界约束。`EmployeeCreate`/`GatewayBinding` 有 `min_length` 约束。

**结论**: 无 P0/P1 问题。P3-R5-2、P3-R5-3、P3-R5-6 为低风险建议项。

---

### 3. src/admin/pages.py

**XSS 防护**: 所有 Python 端动态值经 `_esc()`（`html.escape`）转义。前端 JavaScript `esc()` 函数对 HTML 内容上下文有效。但在 `onclick` 属性内的 JavaScript 字符串上下文中，HTML 实体编码不足以防 XSS（P3-R5-1），当前因数据源服务端控制而不可利用。

**注入攻击**: 无 SQL 注入风险（纯 HTML 渲染）。`p.id` 未转义直接插入 HTML（P3-R5-11），但因始终为整数而安全。

**结论**: 无 P0/P1 问题。P3-R5-1、P3-R5-9、P3-R5-11 为防御性深度建议。

---

### 4. src/kb/store.py

**跨租户隔离（第四轮修复验证）**:
- `delete_corpus`: 已添加 `enterprise_id` 可选参数和跨租户校验 ✓ (P2-R4-1 修复)
- `update_corpus`: 已添加 `enterprise_id` 可选参数和跨租户校验 ✓ (P2-R4-1 修复)
- `confirm_product`: `enterprise_id` 校验商品归属 ✓
- `delete_product`: `enterprise_id` 校验商品归属 ✓
- `retrieve`: Chroma `where` 过滤 + SQLite 回查时校验 `enterprise_id` ✓
- `_filter_product_ids`: `WHERE enterprise_id=?` ✓

**注入攻击**: 所有 SQL 使用参数化查询。表名/列名通过 `_PENDING_COL`、`_MILK_COLS`、`_NUT_COLS` 白名单校验。`_filter_product_ids` 中 `f"{k}=?"` 的列名 `k` 经白名单校验。安全。

**数据泄露**: HQ 只读护栏（`_row_readonly`）正确拦截 HQ 语料的删除/改写。Chroma 删除失败记录 warning 日志（P2-R4-2 修复）✓。

**资源管理**: 全部使用 `db_tx` 上下文管理器，连接正确关闭。`db_tx` 块内冗余 `conn.commit()`（P3-R5-10）。

**异常处理**: Chroma 操作异常均被捕获并记录日志，不静默吞异常。

**结论**: 无 P0/P1 问题。P2-R4-1、P2-R4-2 修复正确完整。

---

### 5. src/kb/models.py

**审查结果**: 纯数据模型定义，无安全风险。`MilkProduct`/`NutritionProduct` 的 `to_chunks`/`meta` 方法仅做数据组织，无外部输入处理。

**结论**: 无问题。

---

### 6. src/common/config.py

**审查结果**: 配置加载使用 `yaml.safe_load`（非 `yaml.load`），防止 YAML 反序列化攻击。环境变量覆盖逻辑清晰，`from_yaml_with_env` 的环境变量优先级高于 YAML 文件。

**输入验证**: `LLMConfig` 的 `temperature`/`max_tokens` 有默认值但无 Pydantic 约束（与 `LLMConfigUpdate` 不同）。低风险，因配置来源为启动时文件/环境变量。

**结论**: 无问题。

---

### 7. src/common/db.py

**审查结果**: `db_tx` 上下文管理器正确实现：`try/except/finally` 确保连接在 `finally` 中关闭，异常时回滚。`connect` 启用 `WAL` 模式和外键约束。`check_same_thread=False` 允许跨线程使用，但因 `db_tx` 每次创建新连接，不存在线程安全问题。

**结论**: 无问题。`db_tx` 是项目正确的数据库连接管理模式。

---

### 8. src/common/crypto.py

**审查结果**: Fernet 加密实现正确。`from_env` 在 `require=True` 时缺密钥抛 `KeyMissing`，禁止静默明文落库。dev key 仅在 `require=False` 时使用并发出 warning。解密失败抛 `InvalidToken` 而非静默返回明文。`get_vault` 单例缓存正确。

**结论**: 无问题。

---

### 9. src/common/egress.py

**审查结果**: 出网白名单策略设计合理。`EgressPolicy` 单例通过 `get_policy` 缓存，`reset_policy` 供测试重置。`AllowedAsyncClient` 包装 `httpx.AsyncClient`，在每次请求前校验白名单。强制开关 `AGENT_EGRESS_ENFORCE` 默认关闭（开发模式），部署时需置 `1`。

**结论**: 无问题。

---

### 10. src/common/embeddings.py

**审查结果**: mock 嵌入使用 MD5 哈希（非加密用途，仅做词袋索引），L2 归一化。`_BgeEmbedder` 惰性加载，插件管理器解析模型路径时异常被捕获。无安全风险。

**结论**: 无问题。

---

### 11. src/common/rerank.py

**审查结果**: `NoReranker` 透传，`BgeReranker` 惰性加载 CrossEncoder。`get_reranker` 按 kind 查表，未知 kind 抛 `NotImplementedError`。无安全风险。

**结论**: 无问题。

---

### 12. src/ingest/importer.py

**认证与授权**: `load_bundle` 校验 `bundle_ent not in (enterprise_id, HQ_ENT)`，拒绝他企 bundle。`scan_and_load` 传入 `enterprise_id` 给 `load_bundle`。

**注入攻击**: 无 SQL 注入（通过 store 层操作）。JSON 解析使用 `json.loads`，安全。

**资源管理**: `_safe_move` 使用 `shutil.rmtree` + `shutil.move`。`_read_lines` 使用 `with open` 正确关闭文件。

**异常处理**: 单条失败不中断整包，记入 `errors` 列表，不静默丢弃。

**结论**: 无问题。

---

### 13. src/baby/store.py

**认证与授权**: 所有查询方法均按 `(enterprise_id, employee_id)` 或 `enterprise_id` 过滤。`list_for_employee` 和 `list_all_for_enterprise` 正确过滤。`find_baby_by_name` 仅匹配 confirmed 且唯一的同名宝宝。

**数据泄露**: 敏感健康字段（`baby_age`/`gender`/`stage`/`allergens_json`/`budget`/`brand_preference_json`/`category`/`health_notes`/`birth_date`/`gestational_weeks`/`medical_history_json`/`feeding_history_json`）在写入时经 `_enc` 加密，读取时经 `_dec` 解密。`name`/`customer_id` 等查询字段保持明文。设计正确。

**资源管理（P2-R5-1）**: 全部 14 个方法使用 `with connect(self.db_path) as conn:` 而非 `with db_tx(self.db_path) as conn:`，导致 SQLite 连接泄漏。这是本轮最重要的新发现问题。

**线程安全**: `_baby_locks` 写锁注册表使用 `_lock_guard` 保护，`_lock_for_baby` 正确实现按 baby_id 串行化。`merge_baby` 按 id 升序加锁防死锁。

**结论**: P2-R5-1（连接泄漏）为本轮核心发现。加密和跨租户隔离设计正确。

---

### 14. src/baby/models.py

**审查结果**: 纯数据模型定义。`BabyProfile.merge` 的列表去重使用 `dict.fromkeys` 保序去重，逻辑正确。`to_prompt_block` 生成注入 system prompt 的档案块，空属性不列，设计合理。

**结论**: 无问题。

---

### 15. tools/dataproc/adapters/_ppstructure.py

**线程安全**: `get_ppstructure` 使用双重检查锁定，`_pp_lock` 保护全局状态。`reset()` 未使用锁（P3-R5-4，与 `_paddle_ocr.py` 不一致）。

**异常处理**: `extract_tables` 缺引擎返回空列表，异常记录 warning 不崩。`TableHTMLParser` 的 `_int_attr` 对非法 colspan/rowspan 值有 try/except 保护。

**结论**: 无 P0/P1 问题。P3-R5-4 为一致性建议。

---

### 16. tools/dataproc/adapters/_paddle_ocr.py

**线程安全**: 双重检查锁定正确实现。`reset()` 使用 `_ocr_lock` 保护。设计规范。

**结论**: 无问题。可作为 `_ppstructure.py` 的参考模板。

---

### 17. tools/dataproc/adapters/pdf.py

**审查结果**: 数字 PDF 直抽（pypdf）+ 扫描件 OCR（PaddleOCR）。OCR 行解包有 `try/except (ValueError, TypeError)` 防御。低置信度标记 `low_conf`。`_render_pages` 使用 `try/finally` 确保 `doc.close()`。

**结论**: 无问题。

---

### 18. tools/dataproc/adapters/image_table.py

**审查结果**: 长图切片 + 预处理 + OCR。OCR 行解包防御与 pdf.py 一致（P1-N4 修复验证通过）。异常处理区分 `OCRDeferred`/`OCRDependencyMissing`（重新抛出）和其他异常（包装为 `RuntimeError`）。

**结论**: 无问题。

---

### 19. tools/dataproc/classifier.py

**审查结果**: 正则规则匹配 + conf.yaml 覆盖。`load_category_overrides` 使用 mtime 缓存，线程安全（`_overrides_lock` 保护读写）。`yaml.safe_load` 安全。无安全风险。

**结论**: 无问题。

---

### 20. tools/dataproc/build.py

**审查结果**: 文件遍历 + 内容解析 + bundle 构建。`_sha_file` 使用 `with open` 正确关闭文件。`_process_nontext` 的异常处理区分 OCR 推迟/依赖缺失/其他异常。`build_bundle` 的外层 `try/except` 记录错误并 `continue`，不崩全局。manifest 包含 checksums 供完整性校验。

**结论**: 无问题。

---

### 21. harness/test_admin_api.py

**测试覆盖**: A1-A21 共 21 个测试，覆盖路由、CRUD、认证、脱敏、跨租户隔离。A19 测试 Bearer Token 认证（无/正确/错误 token）。A20 测试 store 层跨租户拒绝。A21 测试 API 级跨租户拒绝（confirm/delete 商品 → 403）。

**缺失测试（P2-R5-2）**: 跨租户 delete_employee/unbind_gateway/get_baby_detail 的 API 级拒绝路径未测试。跨租户 list_stores 过滤未测试。操作不存在商品的 404 响应未测试。

**测试隔离**: 使用 `tempfile.mkdtemp` 创建临时数据库，`patch.dict(os.environ, ...)` 隔离环境变量。测试后统一清理临时目录。

**结论**: P2-R5-2（测试覆盖不完整）为本轮发现。

---

### 22. harness/test_ppstructure_table.py

**测试覆盖**: T1-T10 共 11 个测试。T3b 现已覆盖 colspan 和 rowspan 两种场景 ✓ (P2-R4-5 修复验证通过)。T9/T10 测试 conf.yaml 覆盖和缓存，测试后正确清理缓存。

**结论**: P2-R4-5 修复完整。无新问题。

---

### 23. scripts/run_harness.py（全量回归入口）

**审查结果**: 测试运行器，使用 `subprocess.run`（列表参数，非 shell=True），无命令注入风险。`resolve_argv` 按空格分割命令字符串，可能对含空格路径有问题，但仅用于测试场景。`read_module_tag` 使用 `errors="ignore"` 读取文件，安全。

**结论**: 无问题。

---

### 24. scripts/secret_scan.py

**审查结果**: 密钥扫描脚本，检查 git 跟踪文件中的真实密钥模式（sk-/github_pat_/ghp_/xoxb-），排除占位符。使用 `subprocess.run`（列表参数）调用 `git ls-files`，安全。测试路径豁免（`_is_test_path`）合理。`.gitignore` 和 `.env.example` 检查正确。

**结论**: 无问题。

---

## 第四轮修复逐项验证详情

### P2-R4-1: delete_corpus / update_corpus 跨租户校验 — 已修复

**位置**: src/kb/store.py 第 364-392 行（delete_corpus）、第 394-443 行（update_corpus）

两个方法均添加了 `enterprise_id: Optional[str] = None` 参数，并在 `_row_readonly` 检查之后添加跨租户校验：

```python
# delete_corpus 第 381-385 行
if enterprise_id is not None and row["enterprise_id"] != enterprise_id:
    raise PermissionError(
        f"跨租户越权: 语料 id={cid} 不属于 enterprise_id={enterprise_id}"
    )
```

```python
# update_corpus 第 415-419 行
if enterprise_id is not None and row["enterprise_id"] != enterprise_id:
    raise PermissionError(
        f"跨租户越权: 语料 id={cid} 不属于 enterprise_id={enterprise_id}"
    )
```

校验顺序正确：先 `_row_readonly`（HQ 只读护栏），后 `enterprise_id`（跨租户校验）。`enterprise_id` 为 Optional 设计，向后兼容，调用方需显式传入才启用校验。修复正确。

---

### P2-R4-2: delete_corpus Chroma 删除日志 — 已修复

**位置**: src/kb/store.py 第 388-392 行

```python
try:
    self.collection.delete(ids=[str(cid)])
except Exception as e:
    # P2-R4-2: 记录警告而非静默忽略
    logger.warning("Chroma 向量删除失败 (corpus id=%s): %s: %s", cid, type(e).__name__, e)
```

与 `delete_product` 的 Chroma 删除日志模式一致。修复正确。

---

### P2-R4-3: list_stores enterprise_id 过滤 — 已修复

**位置**: src/admin/server.py 第 245-254 行

```python
@app.get("/api/stores", dependencies=[Depends(_verify_token)])
def list_stores():
    # P2-R4-3: 过滤当前企业，不暴露其他门店
    with db_tx(admin_db) as conn:
        rows = conn.execute(
            "SELECT enterprise_id, enterprise_name, db_path, created_at "
            "FROM admin_stores WHERE enterprise_id=?",
            (cfg.enterprise_id,),
        ).fetchall()
    return [dict(r) for r in rows]
```

使用参数化查询 + `cfg.enterprise_id` 过滤。修复正确。

---

### P2-R4-4: create_employee / bind_gateway 强制 enterprise_id — 已修复

**位置**: src/admin/server.py 第 284-293 行（create_employee）、第 325-342 行（bind_gateway）

```python
# create_employee 第 287-292 行
def create_employee(emp: EmployeeCreate):
    # P2-R4-4: 强制使用当前实例的企业 ID，忽略请求体中的 enterprise_id
    with db_tx(admin_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admin_employees(enterprise_id, employee_id, employee_name) "
            "VALUES(?,?,?)",
            (cfg.enterprise_id, emp.employee_id, emp.employee_name),  # ← cfg.enterprise_id
        )
```

```python
# bind_gateway 第 327-340 行
def bind_gateway(binding: GatewayBinding):
    # P2-R4-4: 强制使用当前实例的企业 ID
    ent = cfg.enterprise_id
    with db_tx(admin_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admin_employees(enterprise_id, employee_id, employee_name) "
            "VALUES(?,?,?)",
            (ent, binding.employee_id, binding.wechat_name or ""),  # ← ent = cfg.enterprise_id
        )
        conn.execute(
            "UPDATE admin_employees SET wechat_name=?, bot_token=?, bound_at=? "
            "WHERE enterprise_id=? AND employee_id=?",
            (binding.wechat_name, binding.bot_token, time.time(), ent, binding.employee_id),
        )
```

两个端点均使用 `cfg.enterprise_id` 替代请求体中的 `enterprise_id`。修复正确。

---

### P2-R4-5: T3b rowspan 测试 — 已修复

**位置**: harness/test_ppstructure_table.py 第 64-90 行

T3b 现包含 colspan 和 rowspan 两组测试：

```python
# colspan 测试（第 69-78 行）
html_colspan = '''<table>
<tr><td colspan="2">合并表头</td></tr>
<tr><td>A</td><td>B</td></tr>
</table>'''
# ... 断言 ...

# rowspan 测试（第 81-90 行）
html_rowspan = '''<table>
<tr><td rowspan="2">跨行</td><td>1</td></tr>
<tr><td>2</td></tr>
</table>'''
# ... 断言 rowspan 填充 ...
```

修复正确。colspan 和 rowspan 场景均有覆盖。

---

### P2-R4-6: API 级跨租户测试 — 部分修复

**位置**: harness/test_admin_api.py 第 385-409 行（A21）

A21 测试了 confirm/delete 商品的 API 级跨租户拒绝（→ 403），但未覆盖 delete_employee/unbind_gateway/get_baby_detail 的跨租户拒绝路径。详见 P2-R5-2。

---

## 总体评价

### 修复质量评价

**优点**:

1. **第四轮 P2 修复全部到位**: 6 个 P2 问题中 5 个完全修复，1 个部分修复。`delete_corpus`/`update_corpus` 的跨租户校验、`list_stores` 的 enterprise_id 过滤、`create_employee`/`bind_gateway` 的强制 enterprise_id 使用、`delete_corpus` 的 Chroma 日志、T3b 的 rowspan 测试均实现正确。

2. **跨租户隔离已全面覆盖**: 本轮验证确认 server.py 中所有 API 端点均强制使用 `cfg.enterprise_id`，store.py 中 `delete_corpus`/`update_corpus`/`confirm_product`/`delete_product` 均有跨租户校验。无遗漏的 enterprise_id 校验。

3. **无 P0/P1 级别新问题**: 第四轮修复未引入安全漏洞或功能缺陷。SQL 注入、XSS、路径遍历、命令注入等维度均未发现可利用漏洞。

4. **异常处理策略一致**: Chroma 删除失败的两处（`delete_product` 和 `delete_corpus`）均已统一为 `logger.warning` 模式。

**需改进**:

1. **baby/store.py 连接泄漏（P2-R5-1）**: 这是本轮最重要的新发现。`BabyProfileStore` 全部 14 个方法使用 `with connect()` 而非 `with db_tx()`，导致 SQLite 连接不关闭。长期运行的服务端进程会持续累积未关闭连接，可能导致文件描述符耗尽或 "database is locked" 错误。建议统一改用 `db_tx`。

2. **测试覆盖仍有缺口（P2-R5-2）**: A21 仅覆盖 confirm/delete 商品的跨租户拒绝，delete_employee/unbind_gateway/get_baby_detail 的 API 级拒绝路径仍未测试。

3. **P3 问题持续累积**: 多个 P3 问题从第二轮/第三轮/第四轮持续遗留（reset 锁、403 信息泄露、绝对路径检查、scan 异常处理、baby_store 单例、前端错误处理、冗余 commit），建议在后续迭代中逐步清理。

### 安全维度总结

| 维度 | 评分 | 说明 |
|------|------|------|
| 认证与授权 | 5.0 | Bearer Token 全覆盖，跨租户隔离全面（所有 API 端点 + store 层方法） |
| 注入攻击 | 5.0 | SQL 参数化 + 白名单；XSS HTML 转义；路径遍历检查；无命令注入 |
| 数据泄露 | 4.8 | 敏感字段加密 + API 受限字段 + token 脱敏；mask_token 极短 token 边界问题（P3） |
| 资源管理 | 4.5 | db_tx 正确关闭连接；baby/store.py 连接泄漏（P2）；单例模式部分覆盖 |
| 输入验证 | 4.8 | Pydantic 模型验证 + 边界检查；db_path 绝对路径检查缺失（P3） |
| 测试覆盖 | 4.5 | 32/32 全绿；跨租户拒绝路径 API 级测试覆盖不完整（P2） |

### 修复优先级建议

1. **第一优先**（可靠性）: P2-R5-1（baby/store.py 连接泄漏 — 改用 db_tx）
2. **第二优先**（测试补全）: P2-R5-2（补充 delete_employee/unbind_gateway/get_baby_detail 跨租户 API 级测试）
3. **第三优先**（防御加固）: P3-R5-1（esc() JS 属性上下文）、P3-R5-2（mask_token 短 token）、P3-R5-5（403→404 统一）
4. **后续迭代**: P3-R5-3 至 P3-R5-11 + 历史遗留 P3 问题

---

*报告生成时间: 2026-07-22*
*审查文件数: 24*
*第四轮问题验证: P2=5/6 完全修复, 1/6 部分修复*
*新发现问题: P0=0, P1=0, P2=2, P3=11*
*历史遗留未修复: P3=7*
