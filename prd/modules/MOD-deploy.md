# MOD-deploy 模块详解（端侧 1 家 1 实例部署）

> 依据 charter C1 / C4 / C5 / O3′：把整套系统标准化封装为**端侧可部署单元**：1 家企业 = 1 个 Agent 实例，
> 配置驱动企业定制，实例间数据与配置严格隔离。**方案 B 自研**（不依赖 Hermes 运行时）。
> iLink 形态下，端侧实例**仅需注入 bot 凭证 + 放通出网 HTTPS**，无需常驻微信客户端（见架构 D7）。
> 本文件为**可实现规格**。

## 职责
把整套系统标准化封装为**端侧可部署单元**：1 家企业 = 1 个 Agent 实例，配置驱动企业定制，
实例间数据与配置严格隔离，提供启停与升级手段。

---

## 一、部署形态

### 1A Docker 部署（服务器/高配门店）
- **Docker + docker-compose**：每企业一个 compose project / 命名空间（项目名含 `enterprise_id`）。
- **1 家 1 实例**：每企业独立容器 + 独立数据卷 + 独立配置，**物理/逻辑不串**（铁律）。
- **配置即定制**：`conf.yaml` 驱动——产品结构、embedding 模型、LLM provider、iLink bot 凭证、微信策略。

### 1B Windows 直装部署（低配门店，G6+）
- **Python embeddable + 离线 wheels**：不要求门店预装 Python；安装包含 Python 3.11 embeddable。
- **依赖三层分层（核心策略）**：
  - **Tier 1 — 稳定大文件（按 URL 拉取）**：torch CPU (~800MB)、sentence-transformers (~400MB)、
    bge 模型权重 (~100MB)。这些文件迭代缓慢（半年一更）、体积大、且与业务代码解耦。
    **不在安装包中捆绑**，由配置向导 `configure.ps1` 按 `dependency-manifest.yaml` 声明的 URL
    在配置阶段拉取到本地 `wheels/` 和 `models/` 目录。支持：指定 PyPI 镜像、HuggingFace 镜像、
    或直接文件导入（离线 U 盘路径）。
  - **Tier 2 — 小依赖（捆绑在安装包）**：pydantic、pyyaml、chromadb、httpx（共 ~60MB）。
    体积小、更新频率低，捆绑分发比按 URL 拉取更快更可靠。
  - **Tier 3 — 易变代码（可插拔）**：`src/` 应用源码、`deploy/enterprise.yaml` 配置、system_prompt。
    独立于 Python 运行时目录，升级时只需替换 `app/` 目录（`git pull` 或拷贝覆盖），
    不触碰 Python embeddable 和已安装的 wheels。
- **配置向导（端侧模式选择权）**：`configure.ps1` 交互式选择 LLM/embedding 模式后，
  生成 `.env.local` 环境变量文件。`EnterpriseConfig.from_yaml_with_env()` 加载时
  环境变量优先级高于 yaml——**端侧不改 yaml 文件即可切换模式**。
- **1 家 1 实例**铁律不变：每企业独立数据目录 + 独立配置。

---

## 二、端侧网络与凭证（iLink 形态）
- **出网域名白名单**：`ilinkai.weixin.qq.com`（Bot API）+ `novac2c.cdn.weixin.qq.com`（媒体 CDN）。
- **凭证注入**：iLink `bot_token` / `account_id` 通过 secret/env 注入容器，**不打包进镜像**。
- **登录时机**：`qr_login` 二维码流程在初始化阶段人工一次完成（见 MOD-wechat），凭证持久化后常规运行无需人工。
- **无需常驻微信客户端**：iLink 为 bot API，运行期仅出网 HTTPS 长轮询。

---

## 三、数据隔离（红线）
- 每实例独立数据卷：`chroma/`（Chroma 嵌入式知识库持久化目录，**含 HQ 共享库 + B-end 企业库两部分**）+ `kb.db`（SQLite：结构化产品表/会话/FTS5）+ `sessions.db`（会话）+ `weixin/` 凭证 + 日志。
- **HQ 知识库随产品分发到每个实例**（共享、只读）；B-end 库按企业隔离（见 `prd/references/data-model.md`）。
- 镜像**不得打包任何企业数据**；数据只在部署时挂载的卷里（HQ 库由分发流程注入，B-end 库由企业数据装入）。
- 升级保留数据卷并校验兼容性（embedding 模型版本、schema 版本）。

---

## 四、对外契约 / 接口（自研）
- `deploy init <enterprise_id> --config conf.yaml`：按企业配置生成实例（数据卷/环境变量）。
- `deploy up <enterprise_id>` / `deploy down <enterprise_id>`：启停实例。
- `deploy status`：列出当前实例及其健康状态。
- `deploy upgrade <enterprise_id> --image <tag>`：升级镜像，保留数据卷，支持回滚。
- `load_config(enterprise_id) -> EnterpriseConfig`：配置加载（产品结构/LLM/微信凭证等）。

---

## 五、实现步骤
1. **镜像**：构建含全部自研模块（wechat/session/agent/kb/ingest）的 Docker 镜像，版本化 tag。
2. **compose 模板**：每企业渲染独立 project（命名空间 + 数据卷 + secret）。
3. **配置加载**：`load_config` 解析 `conf.yaml`，校验必填（enterprise_id / bot 凭证 / LLM provider）。
4. **生命周期**：`init/up/down/status/upgrade` + 健康探针（HTTP /readyz）。
5. **隔离校验**：实例间数据卷/配置互不可见；升级保留卷 + 兼容性校验。
6. **日志/运维**：结构化日志、实例级监控、失败告警。

---

## 六、关键风险与缓解
| 风险 | 缓解 |
|------|------|
| 多租户共用进程 | **1 家 1 实例**铁律；compose 项目隔离；隔离失败即合规事故 |
| 数据出网违规 | 端侧企业用 Ollama（不出网）；仅显式配置云 API 才出网（C2） |
| 凭证泄露 | bot 凭证走 secret，不进镜像/日志 |
| 升级破坏数据 | 保留数据卷 + embedding/schema 版本校验 + 回滚 |
| 网络不通导致 bot 失效 | 部署前校验出网白名单可达 |
| 实例互踢（同 bot） | 平台锁保证一 bot 仅一处登录（MOD-wechat） |

---

## 七、harness 验收草案（真实运行，非自述）
> 用 docker-compose（或轻量等价：独立进程 + 独立数据目录）驱动，断言隔离/配置/生命周期/升级。

- `test_deploy_isolation.py`：生成的实例含隔离数据卷（kb.db/sessions.db 分离）。
- `test_deploy_config.py`：配置正确加载为 `EnterpriseConfig`（必填校验）。
- `test_deploy_lifecycle.py`：`up` 后健康探针通过、`down` 后释放资源。
- `test_deploy_multi_tenant.py`：两实例数据互不串。
- `test_deploy_upgrade.py`：升级保留数据卷且兼容，回滚可用。
- `test_deploy_network.py`：出网白名单可达性校验（ilinkai / CDN 域名）。
- `test_deploy_env_override.py`：环境变量覆盖 yaml 配置（端侧不改文件即切模式）。
- `test_deploy_manifest.py`：依赖清单校验（Tier1 URL 可达性 + 校验和 + 模式匹配）。
- `test_data_encryption.py`（P0-3）：宝宝健康字段落库密文≠明文 + round-trip + 惰性迁移兼容升级前明文行。
- `test_deploy_egress.py`（P0-2）：出网白名单——默认域名放行 / 强制开启拦截非白名单 / LLM base_url 自动并入 / EXTRA_HOSTS 逃生阀 / 客户端拦截在出网之前。
- `test_secret_scan.py`（P0-1）：仓库无已提交真实密钥 + `.env*` 被 gitignore + `.env.example` 含关键安全变量（CI 可复用 `scripts/secret_scan.py`）。

---

## 八、注意事项 / 雷区
- **1 家 1 实例**是铁律：绝不做多租户共用进程；隔离失败即合规事故。
- 镜像不得打包任何企业数据；数据只在部署时挂载的卷里。
- 升级须保留数据卷并校验兼容性（embedding 模型版本、schema 版本）。
- 端侧实例需确认可出网 `ilinkai.weixin.qq.com` 等域名，否则 bot 无法轮询消息。
- 本模块完全自研（方案 B），不 import Hermes。

---

## 九、P0 安全加固（已落地）

上线前必做的三项安全控制，均已实现并配 harness 红跑通过（详见 `harness/test_data_encryption.py` / `test_deploy_egress.py` / `test_secret_scan.py`）。

### P0-1 密钥环境变量化（收尾）
- `EnterpriseConfig.from_yaml_with_env()` 环境变量覆盖 yaml（端侧不改文件即切模式）。
- 凭证（`bot_token` / `api_key` / 加密密钥）仅经 `env_file: .env.local` 注入容器，**不内联、不打包进镜像**。
- `.gitignore` 忽略 `.env*` / `secrets/`；`deploy/.env.example` 为入库模板（无真实密钥）。
- `scripts/secret_scan.py` 防提交密钥扫描（CI 可挂）。

### P0-2 出入站白名单
- `src/common/egress.py`：`EgressPolicy` + `AllowedAsyncClient`，应用层出网域名强制。
- 默认白名单：`ilinkai.weixin.qq.com`（Bot API）+ `novac2c.cdn.weixin.qq.com`（媒体 CDN）；显式配置的 LLM/Embedding `base_url` 主机自动并入。
- 强制开关 `AGENT_EGRESS_ENFORCE`（默认 `0` 开发透传；部署置 `1` 才拦截）；`AGENT_EGRESS_EXTRA_HOSTS` 逃生阀。
- 接线点：`src/agent/providers.py`（两处 `httpx.AsyncClient`）、`src/wechat/ilink_client.py`（`_post`）。

### P0-3 健康数据加密
- `src/common/crypto.py`：`Vault`（Fernet，密钥 `AGENT_DATA_ENCRYPTION_KEY`）。
- `src/baby/store.py` 在落库边界加密 12 个敏感健康字段（`baby_age`/`gender`/`stage`/`allergens_json`/`budget`/`brand_preference_json`/`category`/`health_notes`/`birth_date`/`gestational_weeks`/`medical_history_json`/`feeding_history_json`）；`name`/`customer_id` 等查询/实体链接必需字段保持明文。
- 升级前明文行（无 `fernet:` 前缀）惰性兼容；生产/部署模式缺密钥启动即抛错（禁止静默明文落库）。
- 开发/mock 未设密钥时用确定性 dev key 并发警告（仅限非生产数据）。
