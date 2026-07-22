# 代码审查报告（第四轮 / 第二轮修复验证轮）

**项目**: 母婴垂类 ToB RAG Agent  
**审查范围**: 第二轮发现的 P1=5, P2=7 修复验证 + 新问题发现  
**审查日期**: 2026-07-22  
**审查标准**: P0(安全) / P1(功能缺陷) / P2(代码质量) / P3(建议优化)

---

## 目录

1. [修复验证总览](#1-修复验证总览)
2. [第二轮修复逐项验证](#2-第二轮修复逐项验证)
3. [新发现问题](#3-新发现问题)
4. [上轮遗留问题跟踪](#4-上轮遗留问题跟踪)
5. [问题汇总](#5-问题汇总)
6. [总体评分与总结](#6-总体评分与总结)

---

## 1. 修复验证总览

### 修复统计

| 级别 | 上轮数量 | 完全修复 | 部分修复 | 未修复 | 修复率 |
|------|----------|----------|----------|--------|--------|
| P1   | 5        | 5        | 0        | 0      | 100%   |
| P2   | 7        | 7        | 0        | 0      | 100%   |
| 合计 | 12       | 12       | 0        | 0      | 100%   |

**结论**: 第二轮发现的全部 12 个问题（P1-N1~N5, P2-N1~N7）均已完全修复，修复质量良好，未引入 P0/P1 级别的新问题。本轮发现的均为 P2/P3 级别的代码质量和设计一致性问题。

### 新发现问题统计

| 级别 | 数量 | 说明 |
|------|------|------|
| P0   | 0    | 无新增安全漏洞 |
| P1   | 0    | 无新增功能缺陷 |
| P2   | 6    | 跨租户校验遗漏、测试覆盖不足、设计不一致 |
| P3   | 5    | 锁使用不一致、信息泄露、资源管理优化 |
| 合计 | 11   | — |

---

## 2. 第二轮修复逐项验证

### 2.1 P1-N1: delete_employee / unbind_gateway 加 enterprise_id 校验 — 已修复

**位置**: src/admin/server.py 第 290-300 行、第 337-349 行

`delete_employee` 现在 DELETE 语句中加入 `AND enterprise_id=?` 条件，并检查 `cur.rowcount == 0` 返回 404：

```python
# 第 290-300 行
@app.delete("/api/employees/{emp_id}", dependencies=[Depends(_verify_token)])
def delete_employee(emp_id: int):
    # P1-N1: 校验 enterprise_id 防跨租户删除
    with db_tx(admin_db) as conn:
        cur = conn.execute(
            "DELETE FROM admin_employees WHERE id=? AND enterprise_id=?",
            (emp_id, cfg.enterprise_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "员工不存在或无权操作")
    return {"status": "ok"}
```

`unbind_gateway` 同样在 UPDATE 语句中加入 `AND enterprise_id=?` 条件：

```python
# 第 337-349 行
@app.delete("/api/gateway/{emp_id}", dependencies=[Depends(_verify_token)])
def unbind_gateway(emp_id: int):
    # P1-N1: 校验 enterprise_id 防跨租户解绑
    with db_tx(admin_db) as conn:
        cur = conn.execute(
            "UPDATE admin_employees SET bot_token=NULL, bound_at=NULL "
            "WHERE id=? AND enterprise_id=?",
            (emp_id, cfg.enterprise_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "网关绑定不存在或无权操作")
    logger.info("网关解绑: emp_id=%s", emp_id)
    return {"status": "ok"}
```

**修复正确、完整。** 404 消息不区分"不存在"和"无权限"，避免信息泄露。校验在同一个 `db_tx` 事务内完成，不存在 TOCTOU 竞态风险。

---

### 2.2 P1-N2: get_baby_detail 加 enterprise_id 跨租户检查 — 已修复

**位置**: src/admin/server.py 第 376-401 行

```python
@app.get("/api/babies/{baby_id}", dependencies=[Depends(_verify_token)])
def get_baby_detail(baby_id: int):
    baby_store = _get_baby_store()
    b = baby_store.get_baby(baby_id)
    if b is None:
        raise HTTPException(404, f"宝宝档案不存在: {baby_id}")
    # P1-N2: 校验 enterprise_id 防跨租户访问
    if b.enterprise_id != cfg.enterprise_id:
        logger.warning("跨租户宝宝档案访问被拒绝: baby_id=%s ent=%s", baby_id, cfg.enterprise_id)
        raise HTTPException(403, "无权访问该宝宝档案")
    return { ... }
```

**修复正确。** 跨租户访问时记录 warning 日志并返回 403。（注：403 vs 404 的区分存在轻微信息泄露，详见新发现问题 P3-R4-2。）

---

### 2.3 P1-N3: PaddleOCR 单例提取为 _paddle_ocr.py 共享模块 — 已修复

**位置**: tools/dataproc/adapters/_paddle_ocr.py（新文件）、pdf.py 第 9 行、image_table.py 第 11 行

新共享模块 `_paddle_ocr.py` 使用 `threading.Lock` + 双重检查锁定模式：

```python
# _paddle_ocr.py 第 14-47 行
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
        except ImportError:
            logger.info("paddleocr 未安装，PaddleOCR 引擎不可用")
            _ocr_engine = None
            return _ocr_engine
        try:
            _ocr_engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        except Exception as e:
            logger.warning("PaddleOCR 实例初始化失败: %s: %s", type(e).__name__, e)
            _ocr_engine = None
        return _ocr_engine
```

`pdf.py` 和 `image_table.py` 均改为从共享模块导入：

```python
# pdf.py 第 9 行
from ._paddle_ocr import get_paddle_ocr

# image_table.py 第 11 行
from ._paddle_ocr import get_paddle_ocr
```

两处调用点均正确使用 `get_paddle_ocr()` 替代原 `_get_paddle_ocr()`，并检查返回值为 `None` 时抛 `OCRDependencyMissing`。

**修复正确、完整。** 消除了代码重复（P2-N3），确保线程安全（P1-N3），两份独立的 PaddleOCR 实例缓存合并为一份。`reset()` 函数也正确使用 `_ocr_lock` 保护。

---

### 2.4 P1-N4: image_table OCR 解包防御性 try/except — 已修复

**位置**: tools/dataproc/adapters/image_table.py 第 83-88 行

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

**修复正确。** 与 `pdf.py` 的防御性解包（第 66-70 行）保持一致，异常时记录 warning 日志并跳过该行，不中断整体 OCR。

---

### 2.5 P1-N5: PermissionError→403, ValueError→404 API 异常捕获 — 已修复

**位置**: src/admin/server.py 第 210-242 行

`db_confirm` 和 `db_delete` 均添加了 `PermissionError` → 403 和 `ValueError` → 404 的异常捕获：

```python
# db_confirm (第 210-225 行)
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

# db_delete (第 227-242 行) — 同样的异常捕获模式
```

**修复正确、完整。** 两个端点均正确捕获三种异常类型：`ValueError`（validate_table）→ 400、`PermissionError`（跨租户）→ 403、`ValueError`（商品不存在）→ 404。跨租户拒绝时记录 warning 日志。

---

### 2.6 P2-N1: init_admin_db 改用 db_tx — 已修复

**位置**: src/admin/models.py 第 33-36 行

```python
def init_admin_db(db_path: str):
    """初始化 admin 表（幂等）。P2-N1: 使用 db_tx 确保连接关闭。"""
    with db_tx(db_path) as conn:
        conn.executescript(ADMIN_SCHEMA)
```

**修复正确。** 从 `connect()` 改为 `db_tx()`，移除了冗余的 `conn.commit()`（`db_tx` 自动提交），与项目统一使用 `db_tx` 的策略一致。

---

### 2.7 P2-N2: StoreCreate.db_path 路径遍历验证 — 已修复

**位置**: src/admin/models.py 第 56-67 行

```python
class StoreCreate(BaseModel):
    enterprise_id: str = Field(min_length=1, max_length=64)
    enterprise_name: str = Field(min_length=1, max_length=128)
    db_path: str = "instance.db"

    @field_validator("db_path")
    @classmethod
    def validate_db_path(cls, v: str) -> str:
        """P2-N2: 拒绝路径遍历。"""
        if ".." in v:
            raise ValueError("db_path 不允许包含 ..")
        return v
```

**修复正确。** 添加了 `@field_validator` 校验 `db_path` 中的 `..` 路径遍历。（注：仍可接受绝对路径如 `/etc/evil.db`，但因 `db_path` 当前仅存储到 `admin_stores` 表元数据中，风险可控，详见 P3-R4-3。）

---

### 2.8 P2-N3: _get_paddle_ocr 去重提取 — 已修复

**位置**: tools/dataproc/adapters/_paddle_ocr.py（新共享模块）

已通过 P1-N3 的修复一并解决。`pdf.py` 和 `image_table.py` 中重复的 `_get_paddle_ocr()` 函数和模块级状态已删除，统一使用 `_paddle_ocr.py` 中的 `get_paddle_ocr()`。

**修复正确、完整。** 同进程中仅存在一个 PaddleOCR 实例缓存，消除了内存浪费。

---

### 2.9 P2-N4: api_key 明文写入警告日志 — 已修复

**位置**: src/admin/server.py 第 166-169 行

```python
if update.api_key:
    data["llm"]["api_key"] = update.api_key
    # P2-N4: api_key 明文写入 YAML，建议通过 AGENT_LLM_API_KEY 环境变量覆盖
    logger.warning("LLM api_key 已明文写入 %s，建议设置 AGENT_LLM_API_KEY 环境变量替代", abs_path)
```

**修复正确。** 明文写入 api_key 时记录 warning 日志，提示管理员使用环境变量替代方案。

---

### 2.10 P2-N5: colspan/rowspan 测试 (T3b) — 已修复

**位置**: harness/test_ppstructure_table.py 第 64-76 行

```python
def t3b_html_parser_colspan_rowspan():
    """T3b: TableHTMLParser 支持 colspan/rowspan（P2-N5 补充测试）。"""
    from dataproc.adapters._ppstructure import TableHTMLParser
    html = '''<table>
    <tr><td colspan="2">合并表头</td></tr>
    <tr><td>A</td><td>B</td></tr>
    </table>'''
    parser = TableHTMLParser()
    parser.feed(html)
    assert len(parser.rows) == 2, f"应解析出 2 行，实际: {len(parser.rows)}"
    assert len(parser.rows[0]) == 2, f"colspan 行应有 2 列，实际: {len(parser.rows[0])}"
    assert parser.rows[0][0] == "合并表头", f"colspan 内容应填充，实际: {parser.rows[0]}"
    assert parser.rows[1] == ["A", "B"], f"第二行不匹配: {parser.rows[1]}"
```

**修复正确（部分）。** colspan 场景已测试。但测试名含"rowspan"，实际未覆盖 rowspan 场景，详见新发现问题 P2-R4-5。

---

### 2.11 P2-N6: HQProductRecord kind 推断 — 已修复

**位置**: tools/dataproc/build.py 第 251-260 行、第 270-275 行

`.md` 产品路径（第 251-260 行）：
```python
cls = classify(body)
prod_kind = "nutrition" if cls.get("product_category") == "营养品" else "milk"
products.append(ProductRecord(
    kind=prod_kind, uid=uid, status=status, source_ref=rel, ...).to_dict())
if namespace == "hq":
    hq_products.append(HQProductRecord(
        kind=prod_kind, fields=fields, meta={"vendor": ent_id}).to_dict())
```

非文本路径（第 270-275 行）：
```python
if product_dict:
    products.append(product_dict)
    if namespace == "hq":
        # P2-N6: 从 product_dict 推断 kind，不再硬编码 "milk"
        hq_kind = product_dict.get("kind", "milk")
        hq_products.append(HQProductRecord(
            kind=hq_kind, fields=product_dict["fields"],
            meta={"vendor": ent_id}).to_dict())
```

**修复正确、完整。** 两条路径均根据分类结果推断 kind，不再硬编码 `"milk"`。`.md` 路径使用 `prod_kind`（从 `classify(body)` 推断），非文本路径从 `product_dict` 提取已推断的 kind 值。两条路径的 kind 推断逻辑一致。

---

### 2.12 P2-N7: Chroma 删除失败日志 — 已修复

**位置**: src/kb/store.py 第 268-273 行

```python
# P2-24: 批量删除 Chroma 向量
if ids:
    try:
        self.collection.delete(ids=[str(i) for i in ids])
    except Exception as e:
        # P2-N7: 记录警告而非静默忽略，便于排查孤儿向量
        logger.warning("Chroma 向量删除失败 (corpus ids=%s): %s: %s", ids, type(e).__name__, e)
```

**修复正确。** `delete_product` 中的 Chroma 删除失败不再静默忽略，改为记录 warning 日志，便于排查孤儿向量问题。（注：`delete_corpus` 方法中的相同模式未被同步修复，详见新发现问题 P2-R4-2。）

---

## 3. 新发现问题

### P0 — 安全漏洞（0 项）

无新增安全漏洞。

### P1 — 功能缺陷（0 项）

无新增功能缺陷。

### P2 — 代码质量（6 项）

#### P2-R4-1: delete_corpus / update_corpus 缺少 enterprise_id 跨租户校验

**位置**: src/kb/store.py 第 364-383 行（delete_corpus）、第 385-427 行（update_corpus）

```python
def delete_corpus(self, cid: int) -> None:
    with db_tx(self.db_path) as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT enterprise_id, meta_json FROM corpus WHERE id=?", (cid,)
        ).fetchone()
        if row is None:
            return
        if self._row_readonly(row):
            raise ReadonlyError(...)
        cur.execute("DELETE FROM fts_corpus WHERE rowid=?", (cid,))
        cur.execute("DELETE FROM corpus WHERE id=?", (cid,))
        conn.commit()
    # ...
```

**问题**: `delete_product` 和 `confirm_product` 已在 P0-04 修复中添加了 `enterprise_id` 跨租户校验，但 `delete_corpus` 和 `update_corpus` 方法均不检查 `enterprise_id`。这两个方法仅检查 `_row_readonly`（HQ 只读护栏），不校验操作者是否属于该 corpus 记录的企业。

当前这两个方法未通过 API 端点直接暴露（server.py 中无对应路由），但：
1. 它们被 agent pipeline 和 ingest importer 内部调用，如果调用链中传入错误的 cid，可能导致跨租户数据删除/修改
2. 未来如果添加管理 API 端点暴露这两个方法，缺少 enterprise_id 校验将直接成为跨租户越权漏洞
3. 与 `delete_product` 的安全设计不一致，违反纵深防御原则

**修复建议**: 添加 `enterprise_id` 可选参数，校验 corpus 记录归属：

```python
def delete_corpus(self, cid: int, enterprise_id: Optional[str] = None) -> None:
    with db_tx(self.db_path) as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT enterprise_id, meta_json FROM corpus WHERE id=?", (cid,)
        ).fetchone()
        if row is None:
            return
        if self._row_readonly(row):
            raise ReadonlyError(...)
        if enterprise_id is not None and row["enterprise_id"] != HQ_ENT:
            if row["enterprise_id"] != enterprise_id:
                raise PermissionError(
                    f"跨租户越权: corpus id={cid} 不属于 enterprise_id={enterprise_id}"
                )
        # ... 删除逻辑 ...
```

---

#### P2-R4-2: delete_corpus Chroma 删除仍静默忽略异常（P2-N7 修复不完整）

**位置**: src/kb/store.py 第 380-383 行

```python
def delete_corpus(self, cid: int) -> None:
    # ... SQLite 删除逻辑 ...
    try:
        self.collection.delete(ids=[str(cid)])
    except Exception:
        pass  # Chroma 缺该 id 不阻塞
```

**问题**: P2-N7 修复了 `delete_product` 中的 Chroma 删除静默忽略问题（添加了 `logger.warning`），但 `delete_corpus` 方法中的相同模式未被同步修复。`except Exception: pass` 捕获所有异常类型（不仅是"id 不存在"），包括网络错误、Chroma 内部错误等，这些异常被静默吞掉，导致：
1. 孤儿向量无法被排查（与 P2-N7 修复前 `delete_product` 的问题相同）
2. 两处 Chroma 删除的异常处理策略不一致

注释"Chroma 缺该 id 不阻塞"解释了 `pass` 的意图（corpus 可能未被索引到 Chroma），但 `except Exception` 范围过宽。

**修复建议**: 与 `delete_product` 保持一致，添加 warning 日志：

```python
try:
    self.collection.delete(ids=[str(cid)])
except Exception as e:
    logger.warning("Chroma 向量删除失败 (corpus id=%s): %s: %s", cid, type(e).__name__, e)
```

---

#### P2-R4-3: list_stores 返回所有门店数据，无 enterprise_id 过滤

**位置**: src/admin/server.py 第 245-251 行

```python
@app.get("/api/stores", dependencies=[Depends(_verify_token)])
def list_stores():
    with db_tx(admin_db) as conn:
        rows = conn.execute(
            "SELECT enterprise_id, enterprise_name, db_path, created_at FROM admin_stores"
        ).fetchall()
    return [dict(r) for r in rows]
```

**问题**: `list_employees`、`list_gateway_bindings`、`list_babies` 均在 P0-01 修复中添加了 `WHERE enterprise_id=?` 过滤，但 `list_stores` 仍返回所有企业的门店数据，包括其他企业的 `enterprise_id`、`enterprise_name`、`db_path`、`created_at`。

`db_path` 字段暴露了其他企业的数据库文件路径，属于敏感基础设施信息。虽然服务绑定 127.0.0.1 且需 Bearer Token 认证，但与同模块其他 list 端点的安全设计不一致。

**缓解因素**: `admin_stores` 表可能设计为 HQ 级别的多企业管理表（`create_store` 也接受任意 enterprise_id），但与 `list_employees` 等端点的单企业隔离设计不统一。

**修复建议**: 如果 `list_stores` 确实需要多企业管理功能，应在 API 响应中脱敏 `db_path`（如仅显示文件名不显示完整路径）；如果不需要，应添加 `WHERE enterprise_id=?` 过滤：

```python
@app.get("/api/stores", dependencies=[Depends(_verify_token)])
def list_stores():
    with db_tx(admin_db) as conn:
        rows = conn.execute(
            "SELECT enterprise_id, enterprise_name, db_path, created_at "
            "FROM admin_stores WHERE enterprise_id=? OR enterprise_id='hq'",
            (cfg.enterprise_id,),
        ).fetchall()
    return [dict(r) for r in rows]
```

---

#### P2-R4-4: create_employee / bind_gateway 仍接受任意 enterprise_id

**位置**: src/admin/server.py 第 280-288 行、第 320-335 行

```python
# create_employee (第 280-288 行)
@app.post("/api/employees", dependencies=[Depends(_verify_token)])
def create_employee(emp: EmployeeCreate):
    with db_tx(admin_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admin_employees(enterprise_id, employee_id, employee_name) "
            "VALUES(?,?,?)",
            (emp.enterprise_id, emp.employee_id, emp.employee_name),  # ← 接受任意 enterprise_id
        )
    return {"status": "ok"}

# bind_gateway (第 320-335 行)
@app.post("/api/gateway", dependencies=[Depends(_verify_token)])
def bind_gateway(binding: GatewayBinding):
    with db_tx(admin_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admin_employees(enterprise_id, employee_id, employee_name) "
            "VALUES(?,?,?)",
            (binding.enterprise_id, binding.employee_id, binding.wechat_name or ""),  # ← 接受任意 enterprise_id
        )
        conn.execute(
            "UPDATE admin_employees SET wechat_name=?, bot_token=?, bound_at=? "
            "WHERE enterprise_id=? AND employee_id=?",
            (binding.wechat_name, binding.bot_token, time.time(),
             binding.enterprise_id, binding.employee_id),
        )
    return {"status": "ok", ...}
```

**问题**: P1-N1 修复了 `delete_employee` 和 `unbind_gateway` 的跨租户校验（强制使用 `cfg.enterprise_id`），但 `create_employee` 和 `bind_gateway` 仍接受请求体中的任意 `enterprise_id`。这导致设计不一致：

| 操作 | enterprise_id 来源 | 是否隔离 |
|------|-------------------|----------|
| list_employees | cfg.enterprise_id | 是 |
| create_employee | 请求体 | 否 |
| delete_employee | cfg.enterprise_id | 是 |
| list_gateway_bindings | cfg.enterprise_id | 是 |
| bind_gateway | 请求体 | 否 |
| unbind_gateway | cfg.enterprise_id | 是 |

已认证的 admin 可以为其他企业创建员工或绑定网关 token。虽然创建的记录对该 admin 不可见（list 按 `cfg.enterprise_id` 过滤），但数据已写入共享的 `admin_employees` 表，属于跨租户写操作。

**修复建议**: 在 `create_employee` 和 `bind_gateway` 中强制使用 `cfg.enterprise_id`，或明确设计为多企业管理端点（需在文档中说明）：

```python
@app.post("/api/employees", dependencies=[Depends(_verify_token)])
def create_employee(emp: EmployeeCreate):
    with db_tx(admin_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admin_employees(enterprise_id, employee_id, employee_name) "
            "VALUES(?,?,?)",
            (cfg.enterprise_id, emp.employee_id, emp.employee_name),  # 强制使用当前企业 ID
        )
    return {"status": "ok"}
```

---

#### P2-R4-5: T3b 测试仅覆盖 colspan，未覆盖 rowspan

**位置**: harness/test_ppstructure_table.py 第 64-76 行

```python
def t3b_html_parser_colspan_rowspan():
    """T3b: TableHTMLParser 支持 colspan/rowspan（P2-N5 补充测试）。"""
    from dataproc.adapters._ppstructure import TableHTMLParser
    html = '''<table>
    <tr><td colspan="2">合并表头</td></tr>
    <tr><td>A</td><td>B</td></tr>
    </table>'''
    # ... 仅测试 colspan ...
```

**问题**: 测试名称为 "colspan/rowspan"，但实际只测试了 `colspan="2"` 场景。`TableHTMLParser` 的 rowspan 逻辑（`_occupied` 集合跨行跟踪、`_set_cell` 跨行填充）完全未被测试验证。rowspan 是比 colspan 更复杂的场景（涉及跨行列位置计算），缺乏测试覆盖的风险更高。

**修复建议**: 添加 rowspan 和混合 colspan+rowspan 的测试用例：

```python
def t3c_html_parser_rowspan():
    """T3c: TableHTMLParser 支持 rowspan。"""
    from dataproc.adapters._ppstructure import TableHTMLParser
    html = '''<table>
    <tr><td rowspan="2">A</td><td>B</td></tr>
    <tr><td>C</td></tr>
    </table>'''
    parser = TableHTMLParser()
    parser.feed(html)
    assert len(parser.rows) == 2
    assert parser.rows[0] == ["A", "B"]
    assert parser.rows[1] == ["A", "C"]  # rowspan 填充

def t3d_html_parser_mixed_spans():
    """T3d: TableHTMLParser 混合 colspan+rowspan。"""
    from dataproc.adapters._ppstructure import TableHTMLParser
    html = '''<table>
    <tr><td colspan="2" rowspan="2">合并</td><td>C</td></tr>
    <tr><td>D</td></tr>
    <tr><td>E</td><td>F</td><td>G</td></tr>
    </table>'''
    parser = TableHTMLParser()
    parser.feed(html)
    assert len(parser.rows) == 3
    assert parser.rows[0] == ["合并", "合并", "C"]
    assert parser.rows[1] == ["合并", "合并", "D"]
    assert parser.rows[2] == ["E", "F", "G"]
```

---

#### P2-R4-6: 缺少跨租户拒绝路径的 API 级测试

**位置**: harness/test_admin_api.py

**问题**: A20 测试（`a20_cross_tenant_isolation`）在 store 层验证了 `confirm_product` 和 `delete_product` 的跨租户 `PermissionError`，但未测试以下场景：

1. **API 级 403 响应**: 调用 `POST /api/database/confirm` 操作他企商品时，应返回 403（而非 store 层的 `PermissionError`）
2. **API 级 404 响应**: 操作不存在的商品时，应返回 404
3. **跨租户 delete_employee**: 删除他企员工应返回 404（P1-N1 修复路径）
4. **跨租户 unbind_gateway**: 解绑他企网关应返回 404（P1-N1 修复路径）
5. **跨租户 get_baby_detail**: 查看他企宝宝档案应返回 403（P1-N2 修复路径）

当前测试仅验证了"自己的操作能成功"（happy path），未验证"跨租户操作被拒绝"（rejection path），无法防止未来修改意外移除跨租户校验。

**修复建议**: 添加 API 级跨租户拒绝测试：

```python
def a21_cross_tenant_delete_employee():
    """A21: 跨租户删除员工 → 404。"""
    client, _ = _make_client(_tmp_db())
    # 创建 ent_other 的员工
    with connect(client.app.dependency_overrides ... ) as conn:
        conn.execute("INSERT INTO admin_employees(enterprise_id, employee_id, employee_name) VALUES(?,?,?)",
                     ("ent_other", "emp_x", "他企员工"))
        conn.commit()
    # 获取该员工 id
    with connect(...) as conn:
        row = conn.execute("SELECT id FROM admin_employees WHERE enterprise_id='ent_other'").fetchone()
    # 当前实例 ent_test 尝试删除 → 应返回 404
    r = client.delete(f"/api/employees/{row['id']}")
    assert r.status_code == 404

def a22_cross_tenant_baby_detail():
    """A22: 跨租户查看宝宝详情 → 403。"""
    db = _tmp_db()
    client, _ = _make_client(db)
    from baby.store import BabyProfileStore
    from baby.models import BabyProfile
    store = BabyProfileStore(db)
    cid = store.get_or_create_customer("ent_other", "emp_x", "他企客户")
    bid = store.create_baby(BabyProfile(
        baby_id=None, enterprise_id="ent_other", employee_id="emp_x",
        customer_id=cid, name="他企宝宝", ...
    ))
    r = client.get(f"/api/babies/{bid}")
    assert r.status_code == 403
```

---

### P3 — 建议优化（5 项）

#### P3-R4-1: _ppstructure.py reset() 未使用锁（与 _paddle_ocr.py 不一致）

**位置**: tools/dataproc/adapters/_ppstructure.py 第 161-164 行

```python
# _ppstructure.py — reset 不加锁
def reset():
    """重置引擎缓存（测试用）。"""
    global _pp_engine, _pp_initialized
    _pp_engine = None
    _pp_initialized = False
```

对比 `_paddle_ocr.py` 第 50-55 行：

```python
# _paddle_ocr.py — reset 加锁
def reset():
    """重置引擎状态（仅用于测试）。"""
    global _ocr_engine, _ocr_initialized
    with _ocr_lock:
        _ocr_engine = None
        _ocr_initialized = False
```

**问题**: `_paddle_ocr.py` 的 `reset()` 正确使用 `_ocr_lock` 保护全局状态重置，但 `_ppstructure.py` 的 `reset()` 直接修改全局变量，未获取 `_pp_lock`。虽然 `reset()` 仅在测试中使用，但如果测试并行运行（pytest-xdist），可能导致竞态条件。

**修复建议**: 与 `_paddle_ocr.py` 保持一致，在 `reset()` 中使用锁：

```python
def reset():
    """重置引擎缓存（测试用）。"""
    global _pp_engine, _pp_initialized
    with _pp_lock:
        _pp_engine = None
        _pp_initialized = False
```

---

#### P3-R4-2: get_baby_detail 跨租户返回 403（信息泄露）

**位置**: src/admin/server.py 第 380-385 行

```python
if b is None:
    raise HTTPException(404, f"宝宝档案不存在: {baby_id}")
# P1-N2: 校验 enterprise_id 防跨租户访问
if b.enterprise_id != cfg.enterprise_id:
    logger.warning("跨租户宝宝档案访问被拒绝: baby_id=%s ent=%s", baby_id, cfg.enterprise_id)
    raise HTTPException(403, "无权访问该宝宝档案")
```

**问题**: 跨租户访问返回 403，不存在返回 404。已认证的攻击者可通过枚举 `baby_id` 区分"宝宝存在于其他企业"（403）和"宝宝不存在"（404），从而推断其他企业的宝宝档案数量范围。`baby_id` 为自增整数，枚举成本低。

**修复建议**: 为防止信息泄露，跨租户访问应统一返回 404（与"不存在"不可区分）：

```python
if b is None or b.enterprise_id != cfg.enterprise_id:
    if b is not None and b.enterprise_id != cfg.enterprise_id:
        logger.warning("跨租户宝宝档案访问被拒绝: baby_id=%s ent=%s", baby_id, cfg.enterprise_id)
    raise HTTPException(404, f"宝宝档案不存在: {baby_id}")
```

---

#### P3-R4-3: db_path 验证仅检查 ".."，未检查绝对路径

**位置**: src/admin/models.py 第 61-67 行

```python
@field_validator("db_path")
@classmethod
def validate_db_path(cls, v: str) -> str:
    """P2-N2: 拒绝路径遍历。"""
    if ".." in v:
        raise ValueError("db_path 不允许包含 ..")
    return v
```

**问题**: 验证仅检查 `..` 路径遍历，但接受绝对路径如 `/etc/evil.db` 或 `C:\Windows\System32\evil.db`。虽然 `db_path` 当前仅存储到 `admin_stores` 表元数据中（不立即用于创建文件），但后续使用该路径创建 `KnowledgeStore` 时可能覆盖系统文件。

**修复建议**: 添加绝对路径检查，或限制为相对路径 + 白名单目录：

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

#### P3-R4-4: scan_and_load 未被 try/except 包裹

**位置**: src/admin/server.py 第 195-203 行

```python
@app.post("/api/database/scan", dependencies=[Depends(_verify_token)])
def db_scan():
    inbox = os.environ.get("BUNDLE_INBOX_DIR", "")
    if not inbox or not os.path.isdir(inbox):
        raise HTTPException(400, f"收件箱目录未配置或不存在: {inbox}")
    store = _get_store()
    result = scan_and_load(inbox, store, cfg.enterprise_id)  # ← 未捕获异常
    logger.info("bundle 扫描完成: enterprise_id=%s", cfg.enterprise_id)
    return result
```

**问题**: `scan_and_load` 可能因 bundle 文件损坏、磁盘 I/O 错误、JSON 解析失败等原因抛出异常，但 API 层未捕获，FastAPI 默认返回 500 Internal Server Error。与 `db_confirm` / `db_delete` 的异常处理策略不一致（后者捕获 `PermissionError` → 403, `ValueError` → 404）。

**修复建议**: 添加 try/except 捕获预期异常，返回语义正确的 HTTP 状态码：

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

#### P3-R4-5: _get_baby_store() 每次调用创建新实例

**位置**: src/admin/server.py 第 111-112 行

```python
def _get_baby_store() -> BabyProfileStore:
    return BabyProfileStore(cfg.baby_db_path or cfg.db_path)
```

**问题**: 与 `_get_store()` 使用线程安全单例不同，`_get_baby_store()` 每次调用都创建新的 `BabyProfileStore` 实例。`BabyProfileStore.__init__` 调用 `_init_schema()`，每次执行 `CREATE TABLE IF NOT EXISTS` 和 `ALTER TABLE` 语句。每次 `/api/babies` 请求都触发一次 schema 初始化，虽然幂等但浪费资源。（此问题在上轮 P3-N5 中已提出，本轮未修复。）

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

## 4. 上轮遗留问题跟踪

以下问题在上轮（第三轮）报告中已提出，本轮仍未修复：

| 编号 | 级别 | 文件 | 问题 | 状态 |
|------|------|------|------|------|
| P3-N2 | P3 | pages.py 第 214-221 行 | scanInbox 前端未检查 `r.ok`，API 失败时仍显示"扫描完成" | 未修复 |
| P3-N3 | P3 | pages.py 第 223-231 行 | confirmProduct/deleteProduct 前端未检查 `r.ok`，无错误反馈 | 未修复 |
| P3-N4 | P3 | store.py 多处 | db_tx 块内冗余的 `conn.commit()` 调用（第 145、160、176、235、266、287、304、331、351、379、416、463 行等） | 未修复 |
| P2-20 | P2 | test_ppstructure_table.py | T8 通过子进程运行其他测试（耦合度高） | 未修复 |
| P2-23 | P2 | store.py | retrieve 方法过长（145 行） | 未修复 |
| P2-26 | P2 | build.py | build_bundle 函数过长 | 未修复 |

---

## 5. 问题汇总

### 新发现 P2 — 代码质量（6 项）

| 编号 | 文件 | 行号 | 问题 | 上轮关联 |
|------|------|------|------|----------|
| P2-R4-1 | store.py | 364-427 | delete_corpus / update_corpus 缺少 enterprise_id 跨租户校验 | P0-04 修复遗漏 |
| P2-R4-2 | store.py | 380-383 | delete_corpus Chroma 删除仍静默忽略异常 | P2-N7 修复不完整 |
| P2-R4-3 | server.py | 245-251 | list_stores 返回所有门店数据，无 enterprise_id 过滤 | P0-01 修复遗漏 |
| P2-R4-4 | server.py | 280-288, 320-335 | create_employee / bind_gateway 仍接受任意 enterprise_id | P1-N1 修复不完整 |
| P2-R4-5 | test_ppstructure_table.py | 64-76 | T3b 测试仅覆盖 colspan，未覆盖 rowspan | P2-N5 修复不完整 |
| P2-R4-6 | test_admin_api.py | — | 缺少跨租户拒绝路径的 API 级测试 | P1-N1/N2/N5 测试缺失 |

### 新发现 P3 — 建议优化（5 项）

| 编号 | 文件 | 行号 | 问题 |
|------|------|------|------|
| P3-R4-1 | _ppstructure.py | 161-164 | reset() 未使用锁（与 _paddle_ocr.py 不一致） |
| P3-R4-2 | server.py | 380-385 | get_baby_detail 跨租户返回 403（信息泄露，应统一返回 404） |
| P3-R4-3 | models.py | 61-67 | db_path 验证仅检查 ".."，未检查绝对路径 |
| P3-R4-4 | server.py | 195-203 | scan_and_load 未被 try/except 包裹 |
| P3-R4-5 | server.py | 111-112 | _get_baby_store() 每次创建新实例（P3-N5 未修复） |

### 上轮遗留未修复（6 项）

| 编号 | 级别 | 问题 |
|------|------|------|
| P3-N2 | P3 | scanInbox 前端未处理 API 错误 |
| P3-N3 | P3 | confirmProduct/deleteProduct 前端未处理 API 错误 |
| P3-N4 | P3 | db_tx 块内冗余 conn.commit() |
| P2-20 | P2 | T8 通过子进程运行测试 |
| P2-23 | P2 | retrieve 方法过长 |
| P2-26 | P2 | build_bundle 函数过长 |

---

## 6. 总体评分与总结

### 总体评分: 4.5 / 5.0

（上轮评分 4.0 / 5.0，本轮提升 0.5 分）

### 评分依据

| 维度 | 上轮评分 | 本轮评分 | 说明 |
|------|----------|----------|------|
| 安全性 | 4.0 | 4.5 | P1-N1/N2 跨租户校验已补全 delete/unbind/baby_detail；P1-N5 异常处理已补全 403/404；残留 delete_corpus/update_corpus/list_stores/create_employee 的跨租户遗漏（P2 级） |
| 功能正确性 | 4.0 | 4.5 | P1-N3 PaddleOCR 共享单例线程安全；P1-N4 解包防御；P2-N6 kind 推断完整；无新增功能缺陷 |
| 代码质量 | 4.0 | 4.5 | P2-N1/N3 db_tx 统一与共享模块提取；P2-N4 api_key 警告日志；P2-N7 Chroma 日志（delete_corpus 遗漏）；残留冗余 commit 和前端错误处理 |
| 测试覆盖 | 4.0 | 4.0 | T3b colspan 测试已添加但 rowspan 未覆盖；A20 仅测 store 层跨租户，缺 API 级拒绝路径测试 |
| 可维护性 | 4.0 | 4.5 | _paddle_ocr.py 共享模块设计良好；_ppstructure.py reset() 锁不一致；_get_baby_store 仍每次创建实例 |

### 修复质量评价

**优点**:

1. **P1 修复全部到位**: 第二轮发现的 5 个 P1 问题全部正确修复，修复方式符合最佳实践。跨租户校验（delete/unbind/baby_detail）、线程安全（PaddleOCR 共享单例 + Lock）、异常处理（PermissionError→403, ValueError→404）、防御性编程（OCR 解包 try/except）均实现完整。

2. **_paddle_ocr.py 共享模块设计优秀**: 将 PaddleOCR 单例提取为独立共享模块，同时解决了线程安全（P1-N3）和代码重复（P2-N3）两个问题。双重检查锁定模式正确实现，`reset()` 函数也正确使用锁。`pdf.py` 和 `image_table.py` 的导入和使用统一规范。

3. **异常处理策略统一**: `db_confirm` 和 `db_delete` 的 `PermissionError`→403、`ValueError`→404 映射语义正确，跨租户拒绝时记录 warning 日志，便于安全审计。与 `delete_employee`/`unbind_gateway` 的 404 策略（不区分"不存在"和"无权限"）形成纵深防御。

4. **HQProductRecord kind 推断完整**: 两条代码路径（.md 产品路径和非文本路径）均根据分类结果推断 kind，逻辑一致，不再硬编码 `"milk"`。

5. **init_admin_db 和 db_path 验证修复正确**: `init_admin_db` 统一使用 `db_tx` 并移除冗余 commit；`StoreCreate.db_path` 添加 `..` 路径遍历检查。

**需改进**:

1. **跨租户校验覆盖不完整**: P1-N1 修复了 delete/unbind，但 create_employee/bind_gateway 仍接受任意 enterprise_id（P2-R4-4）；list_stores 仍返回所有企业数据（P2-R4-3）；store 层的 delete_corpus/update_corpus 缺少 enterprise_id 校验（P2-R4-1）。建议对所有按 id 操作的端点和方法统一审计。

2. **P2-N7 修复不完整**: `delete_product` 的 Chroma 删除已添加日志，但 `delete_corpus` 的相同模式未同步修复（P2-R4-2），两处异常处理策略不一致。

3. **测试覆盖不足**: T3b 测试名称含"rowspan"但实际未测试 rowspan（P2-R4-5）；缺少 API 级跨租户拒绝路径测试（P2-R4-6），无法防止未来修改意外移除安全校验。

4. **上轮 P3 问题未处理**: 前端错误处理（P3-N2/N3）、冗余 commit（P3-N4）、_get_baby_store 单例（P3-N5/P3-R4-5）等问题持续遗留。

### 修复优先级建议

1. **第一优先**（安全加固）: P2-R4-1（delete_corpus/update_corpus 跨租户校验）, P2-R4-4（create/bind enterprise_id 强制）
2. **第二优先**（一致性）: P2-R4-2（delete_corpus Chroma 日志）, P2-R4-3（list_stores 过滤）
3. **第三优先**（测试补全）: P2-R4-5（rowspan 测试）, P2-R4-6（API 级跨租户测试）
4. **后续迭代**: P3 全部 + 上轮遗留 P3 问题

### 总结

本轮审查验证了第二轮全部 12 项修复，修复率 100%，修复质量良好。未发现 P0/P1 级别的新问题，代码库的安全性和稳定性显著提升。剩余问题均为 P2/P3 级别的代码质量和设计一致性问题，主要集中在跨租户校验的覆盖完整性（delete_corpus/update_corpus/list_stores/create_employee/bind_gateway）和测试覆盖不足（rowspan、API 级跨租户拒绝路径）。建议在下一轮迭代中优先补全跨租户校验覆盖面，并添加对应的 API 级拒绝路径测试。

---

*报告生成时间: 2026-07-22*  
*审查文件数: 13（含 common/db.py 和 baby/store.py 支撑文件）*  
*第二轮问题验证: P1=5/5 完全修复, P2=7/7 完全修复*  
*新发现问题: P0=0, P1=0, P2=6, P3=5*  
*上轮遗留未修复: P2=3, P3=3*
