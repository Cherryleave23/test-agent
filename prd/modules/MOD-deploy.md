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
- **Docker + docker-compose**：每企业一个 compose project / 命名空间（项目名含 `enterprise_id`）。
- **1 家 1 实例**：每企业独立容器 + 独立数据卷 + 独立配置，**物理/逻辑不串**（铁律）。
- **配置即定制**：`conf.yaml` 驱动——产品结构、embedding 模型、LLM provider、iLink bot 凭证、微信策略。

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

---

## 八、注意事项 / 雷区
- **1 家 1 实例**是铁律：绝不做多租户共用进程；隔离失败即合规事故。
- 镜像不得打包任何企业数据；数据只在部署时挂载的卷里。
- 升级须保留数据卷并校验兼容性（embedding 模型版本、schema 版本）。
- 端侧实例需确认可出网 `ilinkai.weixin.qq.com` 等域名，否则 bot 无法轮询消息。
- 本模块完全自研（方案 B），不 import Hermes。
