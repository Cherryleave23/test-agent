# MOD-admin — WebUI 管理后台

> 模块编号: MOD-admin  
> 状态: done（5 大板块 + 安全加固 R2 + 20 条 harness GREEN）  
> 依赖: MOD-deploy（配置）, MOD-knowledge-ingest（数据库加载）, MOD-baby-profile（档案查看）  
> 创建: 2026-07-22

## 一、需求概述

为端侧部署的母婴 RAG Agent 提供本地 WebUI 管理后台，替代直接操作配置文件和命令行的运维方式。
后台包含 5 个板块，覆盖日常运维操作。

### Must-have
1. **LLM 选择** — 查看/修改 LLM provider 配置（mock/ollama/cloud），写入 yaml
2. **数据库加载** — 触发 scan_and_load 扫收件箱加载 bundle，查看知识库统计，管理 pending 商品
3. **门店创立与员工管理** — 企业/门店 CRUD + 员工 CRUD
4. **员工微信网关绑定** — iLink Bot Token 绑定/解绑（脱敏展示）
5. **宝宝档案查看** — 列出企业下全部宝宝档案概览，点击查看详情（只读）

### Non-goals（明确不做）
- 不做实时消息监控（那是 agent 运行时的职责）
- 不做数据可视化/报表
- 不做 agent 配置热加载（LLM 变更需重启 agent）

## 二、技术方案

- **后端**: FastAPI（已为项目依赖，无新增）
- **前端**: 原生 HTML + JavaScript（无 React/Vue 构建步骤，零前端工具链依赖）
- **数据存储**: 与 agent 共享 SQLite 库，admin 表名前缀 `admin_`（与业务表隔离）
- **端口**: 默认 8090，绑定 127.0.0.1
- **启动**: `python -m admin.server --config deploy/enterprise.yaml --port 8090`

## 三、API 设计

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/` | 仪表盘 |
| GET/POST | `/api/llm` | 查看/修改 LLM 配置 |
| GET | `/api/database/status` | 知识库统计 |
| POST | `/api/database/scan` | 触发 scan_and_load |
| GET | `/api/database/pending` | 列出待确认商品 |
| POST | `/api/database/confirm` | 确认 pending 商品 |
| DELETE | `/api/database/product` | 删除商品 |
| GET/POST | `/api/stores` | 门店列表/创建 |
| GET/POST | `/api/employees` | 员工列表/创建 |
| DELETE | `/api/employees/{id}` | 删除员工 |
| GET/POST | `/api/gateway` | 网关绑定列表/绑定 |
| DELETE | `/api/gateway/{id}` | 解绑网关 |
| GET | `/api/babies` | 宝宝档案列表 |
| GET | `/api/babies/{id}` | 宝宝档案详情 |

## 四、安全设计（R2 加固）

### 认证
- **Bearer Token 认证**：`AGENT_ADMIN_TOKEN` 环境变量设置后，所有 API 端点需携带 `Authorization: Bearer <token>`
- 未设置环境变量时为开发模式（不启用认证）
- Token 比较使用 `secrets.compare_digest()` 防时序攻击

### 租户隔离
- 所有列表 API（employees/gateway/babies）强制使用 `cfg.enterprise_id`，不接受外部传入参数
- `confirm_product` / `delete_product` 校验商品归属，跨租户操作抛 `PermissionError`

### XSS 防护
- 服务端：所有动态值经 `html.escape()` 转义（`& < > " '`)
- 客户端：统一 `esc()` 函数，转义 `& < > " '`，提取为公共 `_BASE_JS` 片段
- API 响应插入 DOM 使用 `textContent` / `createElement`，不直接拼 `innerHTML`

### 路径安全
- YAML 配置路径验证：拒绝 `..` 路径遍历，仅允许 `.yaml`/`.yml` 后缀
- 表名白名单：`ALLOWED_TABLES = frozenset({"products_milk", "products_nutrition"})`

### 数据脱敏
- Token 脱敏：保留前 4 + 后 4 字符，中间用 `*` 填充
- LLM api_key：GET 时返回 `<set>` 占位，不泄露真实 key；POST 时空 key 不覆盖已有 key
- 宝宝档案：列表只展示概览，详情排除 allergens/medical_history/feeding_history/health_notes

### 连接管理
- `server.py` 使用 `db_tx` 替代 `connect()`，确保连接关闭
- `_get_store()` 使用 `threading.Lock` 双重检查，线程安全单例
- `store.py` 全部 `with connect()` 替换为 `with db_tx()`，修复连接泄露

## 五、Harness 验收

| ID | 断言 | 文件 |
|----|------|------|
| A1 | create_app 路由含 5 大板块 | test_admin_api.py |
| A2 | GET /api/llm 返回 LLM 配置 | test_admin_api.py |
| A3 | POST /api/llm 写入 yaml | test_admin_api.py |
| A4 | GET /api/database/status 返回统计 | test_admin_api.py |
| A5 | GET /api/stores 初始空列表 | test_admin_api.py |
| A6 | POST /api/stores 创建门店可查 | test_admin_api.py |
| A7 | POST /api/employees 创建员工可查 | test_admin_api.py |
| A8 | POST /api/gateway 绑定后 token 脱敏 | test_admin_api.py |
| A9 | GET /api/babies 返回宝宝列表 | test_admin_api.py |
| A10 | GET / 返回仪表盘 HTML | test_admin_api.py |
| A11 | DELETE /api/employees/{id} 删除员工 | test_admin_api.py |
| A12 | DELETE /api/gateway/{id} 解绑网关 | test_admin_api.py |
| A13 | POST /api/database/confirm 确认商品 | test_admin_api.py |
| A14 | DELETE /api/database/product 删除商品 | test_admin_api.py |
| A15 | GET /api/babies/{id} 详情无敏感字段 | test_admin_api.py |
| A16 | POST /api/database/scan 无收件箱 400 | test_admin_api.py |
| A17 | GET /api/babies/{id} 不存在 404 | test_admin_api.py |
| A18 | POST /api/database/confirm 非法表名 400 | test_admin_api.py |
| A19 | Bearer Token 认证（无/正确/错误） | test_admin_api.py |
| A20 | 跨租户隔离（confirm/delete 拒绝越权） | test_admin_api.py |

## 六、数据转化补全（P2/P3）

### PP-Structure 表格识别（P2）
- `pdf.py`: 替换 stub 为真实 `PPStructure` 引擎调用，解析 HTML 表格为二维 cells 数组
- `image_table.py`: 新增 `_extract_tables_from_image` 函数，对原图跑 PP-Structure 版面分析+表格识别
- 缺 paddleocr 时保持 `table_pending` 占位，不阻断文本 OCR
- `test_dataproc_pdf.py`: 修复 fitz 硬依赖（FITZ_OK 门控，缺失时跳过 I7/I11）

### 分类器（P3）
- 新增 `tools/dataproc/classifier.py`：
  - `classify_ptype()`: 关键词规则推断奶粉类型（羊奶粉/有机奶粉/水解蛋白/氨基酸/早产儿/牛奶粉）
  - `classify_category()`: 推断商品大类（配方粉/营养品/辅食/日用品）
  - `classify()`: 完整分类，支持 conf.yaml 自定义覆盖
- 集成到 `build.py` `_process_nontext` 流程
- 正则预编译 + `threading.Lock` 线程安全缓存

### Harness（P5）
| ID | 断言 | 文件 |
|----|------|------|
| T1 | PP-Structure 共享模块可导入 | test_ppstructure_table.py |
| T2 | extract_tables 空输入返回空列表 | test_ppstructure_table.py |
| T3 | TableHTMLParser 解析 HTML 表格 | test_ppstructure_table.py |
| T4 | classify_ptype 推断奶粉类型 | test_ppstructure_table.py |
| T5 | classify_category 推断商品大类 | test_ppstructure_table.py |
| T6 | classify 返回完整分类结构 | test_ppstructure_table.py |
| T7 | ImageTableAdapter 无 OCR 抛 OCRDeferred | test_ppstructure_table.py |
| T8 | test_dataproc_pdf 行为测试（退出码 0） | test_ppstructure_table.py |
| T9 | conf.yaml 覆盖路径生效 | test_ppstructure_table.py |
| T10 | conf.yaml 缓存生效 | test_ppstructure_table.py |

全量门禁: 32/32 ALL GREEN
