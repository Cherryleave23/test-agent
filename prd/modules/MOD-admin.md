# MOD-admin — WebUI 管理后台

> 模块编号: MOD-admin  
> 状态: done（5 大板块 + 安全加固 + 18 条 harness GREEN）  
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
- 不做认证/权限（本地 127.0.0.1 绑定，内网信任）
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

## 四、安全考量

- Token 脱敏：API 返回 bot_token 时截断为前 8 字符 + `…`
- 本地绑定：默认监听 127.0.0.1，不暴露外网
- LLM api_key：GET 时返回 `<set>` 占位，不泄露真实 key
- 宝宝档案：列表只展示概览（姓名/月龄/性别/阶段/状态），详情需点击查看

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

## 六、数据转化补全（P2/P3）

本次同时补全了数据转化模块的未完成项：

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

### Harness（P5）
| ID | 断言 | 文件 |
|----|------|------|
| T1 | PP-Structure 函数存在且可导入 | test_ppstructure_table.py |
| T2 | _extract_tables 空输入返回空列表 | test_ppstructure_table.py |
| T3 | _TableHTMLParser 解析 HTML 表格 | test_ppstructure_table.py |
| T4 | classify_ptype 推断奶粉类型 | test_ppstructure_table.py |
| T5 | classify_category 推断商品大类 | test_ppstructure_table.py |
| T6 | classify 返回完整分类结构 | test_ppstructure_table.py |
| T7 | ImageTableAdapter 无 OCR 标 table_pending | test_ppstructure_table.py |
| T8 | test_dataproc_pdf fitz 门控 | test_ppstructure_table.py |

全量门禁: 32/32 ALL GREEN
