# MOD-wechat 模块详解（个人微信接入层 · 腾讯官方 iLink Bot API）

> 依据 charter C1 / C5 / O3′：个人微信经**腾讯官方 iLink Bot API** 接入；**方案 B 自建网关**，
> Hermes `weixin.py` 仅作**参考实现（只读不 import）**。证据：`NousResearch/hermes-agent`
> 的 `gateway/platforms/weixin.py`（`WeixinAdapter`，注释原文 "via Tencent's iLink Bot API"）。
> 本文件是**可实现规格**，进入编码时按此落地并配齐 harness。

## 职责
最外层适配器：以**同一个 iLink bot 账号**接收多名员工各自个人微信发来的消息，按 iLink 消息中的
**`from_user_id`** 识别员工身份，把消息路由到 MOD-session 的独立会话，再把回答回发对应员工。
自身不含业务逻辑。一个 agent 实例服务 N 员工（已源码证实，非 1 对 1）。

---

## 一、iLink Bot API 契约（具体，来自源码精读）

### 1.1 接入点与安全
- **Base URL**：`https://ilinkai.weixin.qq.com`；媒体 CDN：`https://novac2c.cdn.weixin.qq.com/c2c`
- **鉴权头（每次请求必带）**：
  ```
  Content-Type: application/json
  AuthorizationType: ilink_bot_token
  Authorization: Bearer {token}
  iLink-App-Id: bot
  iLink-App-ClientVersion: <(2<<16)|(2<<8)|0>   # 即 0x020200
  X-WECHAT-UIN: <random>
  ```
- **凭证**：`account_id`(`ilink_bot_id`) + `token`(`bot_token`) + `base_url`(登录时可能被重定向) +
  `user_id`(`ilink_user_id`)；持久化到 `{account_id}.json`（文件权限 **0600**）。
- 超时（参考值）：长轮询 35s、普通 API 15s、config 10s、二维码 35s。

### 1.2 端点（7 个）
| 方法 | 路径 | 用途 | 关键参数 / 返回 |
|------|------|------|------------------|
| POST | `ilink/bot/getupdates` | 长轮询拉消息 | body `{"get_updates_buf":"<cursor>"}`；返回 `{ret, msgs, get_updates_buf, longpolling_timeout_ms, errcode, errmsg}` |
| POST | `ilink/bot/sendmessage` | 发消息 | body `{"msg":{from_user_id:"", to_user_id, client_id, message_type, message_state, item_list, context_token?}}` |
| POST | `ilink/bot/sendtyping` | 打字状态 | — |
| POST | `ilink/bot/getconfig` | 取配置 | — |
| POST | `ilink/bot/getuploadurl` | 取上传 URL | → CDN `.../upload?encrypted_query_param=...` |
| GET  | `ilink/bot/get_bot_qrcode?bot_type=3` | 取登录二维码 | 返回 `qrcode`(hex) + `qrcode_img_content`(可扫 URL) |
| GET  | `ilink/bot/get_qrcode_status?qrcode=...` | 轮询扫码状态 | `wait / scaned / scaned_but_redirect / confirmed / expired` |

### 1.3 错误码
- `ret`/`errcode` = **-14**：会话过期 → 退避 + 暂停（参考实现暂停 10 分钟）后重登录。
- `ret`/`errcode` = **-2**：频率限制 / 陈旧会话 → 退避重试（熔断）。
- 正常：`ret=0, errcode=0`。

### 1.4 消息模型
- **入站**：`from_user_id`(=员工身份) · `message_id`(去重) · `context_token`(续对话) ·
  `item_list`(type: text/image/video/file/voice) · `chat_type`(dm/group) · `sync_buf`(轮询游标)。
- **出站 sendmessage**：必须回带最新 `context_token`（按 `account + peer` 持久化缓存），否则对话失续；
  文本项结构 `item_list:[{type:"text", text_item:{text}}]`，消息态 `message_state=finish`。

### 1.5 登录流程（qr_login）
```
GET 二维码 ──► 展示(URL/ASCII) ──► 轮询 get_qrcode_status:
   wait ─► 继续等
   scaned ─► 提示确认
   scaned_but_redirect ─► 改 base_url 为 redirect_host
   expired ─► 刷新二维码(≤3 次)
   confirmed ─► 取 {ilink_bot_id, bot_token, baseurl, ilink_user_id} 并持久化
```
> `bot_type=3`；超时 480s；二维码最多刷新 3 次。

---

## 二、对外契约 / 接口（自研，借鉴 Hermes 适配器形态）
- `connect() -> bool`：校验 `token`/`account_id`，启动轮询任务。
- `on_message(msg) -> (enterprise_id, employee_id, text)`：`employee_id = from_user_id`。
- `send(employee_id, text, context_token)`：回发，须回带 `context_token`。
- `disconnect()`：取消轮询、释放平台锁。
- `qr_login() -> credentials`：扫码登录取得 bot 凭证（仅初始化时人工一次）。

---

## 三、实现步骤（自建网关，按此落地）
1. **凭证管理**：加载/保存 `{account_id}.json`（0600），支持重定向 `base_url`。
2. **登录**：实现 `qr_login` 流程（含 `scaned_but_redirect` 改址、`expired` 刷新 ≤3 次）。
3. **轮询循环**：`getupdates` + `sync_buf` 持久化续传；`ret/errcode=-14` 退避 10 分钟；其它错误指数退避。
4. **消息处理**：解析 `from_user_id`；`message_id` 去重 + content MD5 指纹二次去重；媒体下载并 AES 解密（CDN）。
5. **回复**：`sendmessage` 回带 `context_token`；长消息分块（~1800 字阈值）；限流熔断 + 退避重试；可选打字状态。
6. **配对/绑定**：`dm_policy` ∈ {`disabled`, `allowlist`, `pairing`, `open`}；自研 `PairingStore`
   （`list_approved` / `approve_code` / `revoke`），**按实例（企业）隔离**。

---

## 四、关键风险与缓解
| 风险 | 缓解（来自源码） |
|------|------------------|
| 会话过期（-14） | 退避 + 暂停重登录；`sync_buf` 游标续传不丢消息 |
| 频率限制（-2） | 熔断（circuit breaker）+ 指数退避重试 |
| 消息重复投递 | `message_id` 去重 + content 指纹去重，双保险 |
| 断线 | `sync_buf` 游标持久化，重连续拉；长轮询超时按空结果处理 |
| 媒体加密 | AES 解密 + CDN 上传/下载（借鉴 `_upload_ciphertext`/`_download_and_decrypt_media`） |
| 多实例 bot 互踢 | 平台锁（`_acquire_platform_lock('weixin-bot-token', token)`），一 bot 仅一处登录 |
| 群聊不到达 | iLink bot 普通群消息可能不到达 → **首版按 DM 模型**设计 |
| 凭证泄露 | 持久化文件权限 0600；不落日志明文 token |

---

## 五、harness 验收草案（真实运行，非自述）

> CI 不能打真实微信。做法：**起一个 mock iLink HTTP 服务**（fake `getupdates`/`sendmessage`/二维码端点），
> 让网关连它，断言行为。每个用例一个 `@wechat` 脚本，确定性 PASS/FAIL。

- `test_wechat_identity.py`：mock 推 `from_user_id=X` 消息 → 断言网关解析出 X 并正确路由。
- `test_wechat_context_token.py`：mock 在入站带 `context_token` → 断言出站 `sendmessage` 回带该 token。
- `test_wechat_dedup.py`：同 `message_id` 推两次 → 断言仅处理一次（无重复回答）。
- `test_wechat_resume.py`：模拟断线后带旧 `sync_buf` 重连 → 断言不丢不重。
- `test_wechat_rate_limit.py`：连续返回 `-2` → 断言触发熔断 + 退避，不复发洪泛。
- `test_wechat_pairing.py`：`allowlist` 拒非白名单；`pairing` 需先绑定才放行。
- `test_wechat_scope.py`：仅响应本实例 bot，忽略其它账号消息。
- `test_wechat_login.py`：mock 二维码流程 `wait→confirmed` → 断言取得并持久化凭证（0600）。
- `test_ilink_client.py`（@module wechat，**已落地**）：生产闭环【真实】ILinkClient 代码路径验证（本地 stub iLink 服务，不依赖真微信，做法同本节开头）。
  - **W1 getupdates 解析**：messages / sync_buf / context_token 正确解析。
  - **W2 sendmessage 回带 context_token**：出站请求体含 to_user_id 与 context_token（续对话连续性）。
  - **W3 限流 429 退避**：服务端返 429 → 客户端抛 RateLimitError 并优雅退避（不洪泛、不崩）。
  > 注：`test_e2e_closed_loop.py` 的 H1–H5 已用 MockILinkServer 验证网关路由/隔离/去重/防幻觉；
  > 本 `test_ilink_client.py` 补的是**真实客户端解析层**的契约，二者互补。

---

## 六、注意事项 / 雷区
- ✅ **官方协议**：iLink Bot API 为腾讯官方开放协议，封号风险远低于逆向方案（O3 已消解）。
- 身份可信来源：依赖 iLink 提供的 `from_user_id`，不在消息体里信任任何自定义 id（防冒充）。
- 多实例隔离：同企业多实例须保证 bot 账号只在一处登录（平台锁），避免互踢。
- **群聊不在首版范围**：iLink bot 普通群消息可能不到达，按 DM 模型设计（见 1.5 群聊限制）。
- 凭证安全：持久化 0600，不打印明文 token。

## 七、实现策略（已定：方案 B，见 O3′ / C5）
- **仅借鉴 iLink 契约自建轻量网关**：Hermes `weixin.py` 仅作**参考实现**（端点/鉴权头/`context_token`/
  `sync_buf`/限流/配对思路），**微信网关、会话隔离、RAG agent 核心全部自研**，不依赖 Hermes 运行时。
- 参考实现位置：`NousResearch/hermes-agent` → `gateway/platforms/weixin.py`（`WeixinAdapter`）。只读不依赖。
