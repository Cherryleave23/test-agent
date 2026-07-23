# 知识转化·爬虫模块设计（独立初级数据获取工具，非 agent 内部数据源）

> ## ⚠️ 架构红线（用户纠正，2026-07-23）
> - **agent 端不含任何爬虫**：agent 的 RAG / 商品库数据**只能**来自数据处理工具
>   （`tools/dataproc`）处理后的 NDJSON bundle，由 `src/ingest/importer.load_bundle` 加载。
>   已落地的剥离：`src/ingest/adapters.WebCrawlerAdapter`（含 `_VisibleTextExtractor`/`_chunk_text`）
>   已**彻底删除**，`src/ingest/protocol.py` 文档同步修正，`harness/test_ingest.py` 的 I1 爬虫用例已移除
>   （改以 `TextAdapter` 验证归一/去重）。`test_ingest.py` 现 5/5 ALL GREEN。
> - **爬虫 = 独立的初级数据获取工具**（dataproc 侧，`tools/dataproc/crawler.py`，零 `src.*` 依赖）：
>   它**获取数据**，但**不**作为系统内部数据源自动喂给 agent。爬到的数据由**用户手动**放入
>   dataproc 的「产品知识」（`产品资料/`），再经 dataproc 处理 → 打包 → 入包 → 被 agent 消费。
> - 一句话数据流向：
>   `爬虫(获取) → 用户手动放入 产品资料 → dataproc build(结构化为结构化产品/语料) → bundle → agent(纯消费)`

## P1 — 意图（本次范围与缺口）

| # | 缺口 | 性质 |
|---|---|---|
| G0 | agent 侧爬虫（WebCrawlerAdapter）违反"agent 只消费 dataproc 数据"红线 | **已剥离（见上）** |
| G1 | dataproc 侧**无独立爬虫工具**——无法把（企业自有/已授权）母婴网页转成可供用户放入产品知识的素材 | **功能缺口（核心）** |
| G2 | 抽取/补全：复用 `structurer._rule_extract`（规则种子）+ `structure()`（范式② LLM 补全，可选 provider）；`build` 不改，零回归 | **复用增强** |
| G3 | 合规约束（PRD §六：仅采集企业自有/已授权内容）：allowlist 域名 + robots 开关 + UA 标识 + 超时/限流 | **合规必需** |

Non-goals：换 HTTP 栈（坚持标准库 `urllib`+`html.parser`，零外部依赖，端侧友好）；真实联网测试
（harness 用可注入 transport / 本地 stub HTTP）；PDF/图片表格爬虫（仍按计划 PaddleOCR/MinerU）；
**自动把爬取结果灌入 agent / 产品资料**（这违反红线，必须由用户手动完成）。

## P2 — 框架（复用 + 极小新增，且保持"工具"而非"内部管线"）

- 新增 `tools/dataproc/crawler.py`（**零 `src.*` 依赖**）：
  - 解析：`_html_to_text(html) -> (title, text)`，极简 `html.parser`（剔除 script/style，块级标签断行）。
  - 抽取：`_rule_extract`（直接复用 `structurer._rule_extract`）作规则种子。
  - 补全：`structure(body, provider)`（复用范式②）——有 provider 则 LLM 补全 + 冲突标 `needs_review`；无 provider 则纯规则。
  - 主入口：`crawl_url(url, out_dir, ...)` → 写 md 到 **抓取暂存**（`out_dir`，默认 `<repo>/.dataproc/crawl_inbox/`），**不**直接写入 `产品资料`。
- 扩展 `tools/dataproc/cli.py`：加 `crawl` 子命令（`--url`、`--repo-dir`、`--out`(默认 crawl_inbox)、`--llm`(接配置里的 provider)、`--allow-domain`）。
- **不改 `build.py` / 不自动导入**：`build` 只遍历三大总文件夹；`crawl_inbox` 不被 `build` 消费，必须由用户手动移入 `产品资料/` 才进入处理管线——以此强制"手动放入"语义。

## P3 — 接口与契约

### `CrawlResult`（dataclass）
```python
@dataclass
class CrawlResult:
    url: str
    title: str
    saved_path: str        # 写入的 md 路径（默认在 crawl_inbox 下）
    fields: dict           # 规则种子 或 LLM 补全后字段
    needs_review: bool     # 范式② 冲突标记
    chunks: int            # 正文分块数（反馈用）
    status: str            # "ok" | "blocked" | "error"
    error: str = ""
```

### 主入口
```python
def crawl_url(url: str, out_dir: str,
              provider=None,                 # ToolLLMProvider；None=纯规则
              allow_domains: List[str] = None,
              respect_robots: bool = False,
              timeout: int = 15,
              user_agent: str = "Mozilla/5.0 (compatible; BabyAgentIngest/1.0)",
              transport=None) -> CrawlResult:
    """抓取 url → 解析 → 规则/LLM 抽取 → 写入 <out_dir>/<title>.md（抓取暂存）。
    transport 可注入（harness 用 stub，生产用真实 urllib）。
    非 allowlist 域名 → status='blocked'，不写文件（合规）。
    网络/解析异常 → status='error'，不写脏文件（不静默丢弃，不谎称成功）。"""
```

### 落盘 md 格式（与 `_parse_md_product` 契约一致；位于 crawl_inbox）
```markdown
---
title: <页面标题>
url: <源 url>
source: web
acquired_by: crawler        # 标记来源，便于用户/审计识别"这是爬来的，需人工确认后入库"
needs_review: <true|false>
brand: <值或空>
stage: <值或空>
net_content: <值或空>
reg_number: <值或空>
age_range: <值或空>
manufacturer: <值或空>
---

<正文（可见文本，按段）>
```

### 融合/补全规则（复用范式②）
- `rule = _rule_extract(body)`（规则种子，从正文提取，不编造）。
- 若有 `provider`：`st = structure(body, provider)` → `fields = st.fields`，`needs_review = st.needs_review`（权威字段冲突保守保留规则值）。
- 若无 `provider`：`fields = rule`，`needs_review = False`。

### 合规（PRD §六）
- `allow_domains`：校验 `url` 主机，不在列表 → `status="blocked"`。默认**拒绝一切**，必须显式授权（企业自有域名/已授权源）。
- `respect_robots`：默认 `False`（企业自有站点）；为 `True` 时先取 `/robots.txt` 判定 `Disallow`。
- `user_agent`：标识 `BabyAgentIngest`，便于站点识别与合规。
- `timeout` + 最小请求间隔（限流）避免打爆目标站。

### "手动放入"语义（红线保障）
- 爬虫只产出 `crawl_inbox/*.md`，**绝不**触碰 `产品资料/`。
- 用户审阅后，通过 dataproc GUI「上传」或文件移动，把 md 放进 `产品资料/`（或 `知识类文章/`）。
- 之后 `dataproc build` 才把它结构化为 `ProductRecord` 并打进 bundle → 入包 → agent 纯消费。
- 抓取 md 带 `acquired_by: crawler` 标记，便于在 GUI 中提示"需人工确认后再入库"。

## P5 — 验收（harness，必须 RUN PASS/FAIL）
`harness/test_crawler.py`（**真实可执行**，用注入 transport 的 stub HTTP，不真实联网）：

| 测试 | 验证点 | 判定 |
|---|---|---|
| C1 | `crawl_url`（stub transport）→ `crawl_inbox/<title>.md` 写出；front-matter 含 `_rule_extract` 字段；md 可被 `_parse_md_product` 正确读回 | PASS/FAIL |
| C2 | 路由提示：含产品信号（brand/stage/...）→ front-matter `meta.kind` 建议 `产品资料`；否则 `知识类文章`（仅写标记，不移动文件） | PASS/FAIL |
| C3 | 给定 `provider=MockProvider(json)` → 调 `structure()`，`fields` 被 LLM 补全，`needs_review` 写入 front-matter | PASS/FAIL |
| C4 | 合规：`url` 不在 `allow_domains` → `status="blocked"`，**不写任何文件** | PASS/FAIL |
| C5 | 端到端（模拟用户手动）：`crawl_url` → 把 inbox md 移入 `产品资料/` → `build_bundle(repo)` → `products.ndjson` 含该结构化产品；`manifest.counts.products>=1` | PASS/FAIL |
| C6 | HTML 解析：`<script>/<style>` 内容被剔除；`<title>` 正确提取；块级标签断行 | PASS/FAIL |
| C7 | 容错：transport 抛网络异常 → `status="error"` + `error` 非空，**不写脏文件**，不崩 | PASS/FAIL |

**闸门**：任一 FAIL ⇒ 未完成；全绿 ⇒ 爬虫工具可用（独立获取源，产出可被用户手动放入产品知识）。
**回归**：跑 `harness/test_paradigm2.py`、`harness/test_dataproc_ocr.py`、`harness/test_dataproc_resolver.py`、`harness/test_ingest.py` 确认零连带影响（agent 爬虫已剥离、build 未改）。

## 后续（非本阶段，列出供排期）
- F1：dataproc GUI「添加网址」按钮（后端 `/crawl` 端点 + 前端输入）→ 产出落入 `crawl_inbox`，并在「产品资料」导入区提示"需人工确认"。
- F2：扩展 `build` md 分支读取 `needs_review` front-matter → 产品 `status="needs_review"`（与范式②非 text 路径一致）；及 md 分支也跑 `structure()`（provider 存在时）。
- F3：批量/`sitemap` 抓取 + 进度反馈（对标 GUI process 进度回调）。
- **一致性观察（待用户决策）**：agent 侧 `IngestPipeline` 的 `MarkdownProductAdapter` / `TextAdapter` 也**绕过 dataproc**直接写 store（属人工/运营录入，非爬虫）。若严格执行"agent 只消费 dataproc 数据"，可后续把这类直接入库也改为"先落 dataproc 仓库 → build → 入包"。本阶段未动（保留人工录入通道，且 `test_ingest.py` I2/I6 仍依赖之）。
