# 图片原生 / 版面复杂资料解析板块（dataproc 解析 Tier 设计）

> 目标：补齐 `dataproc` 对「图片原生」与「版面复杂」两类资料的解析能力。
> 背景缺口：PB-1（图片无 OCR → 空占位）、D4（PP-Structure 表格识别禁用 → 成分表结构丢失）、
> D6（离线端侧首次需联网下载模型）、D11（GUI 无法预览 PDF/图片核对）。
> 关联决策：CVC 纪律（PRD 为准、Tier 可插拔、诚实搬运、pending 待确认闭环）、端侧 1 家 1 实例、合规不出域。
> 研究依据：PaddleOCR-VL（arXiv 2510.14528，2025-10）、PaddleOCR-VL 官方文档、PP-OCRv6 官方文档（2026-06）。

---

## 1. 问题定义：两类资料当前都被"降级处理"

| 资料类型 | 含义 | 当前 `dataproc` 行为 | 结果 |
|---|---|---|---|
| **图片原生** | 整份资料本身就是一张图（产品外包装照、原料标签照、手写配方卡、展会拍的说明书） | 走 `_process_nontext` → `IMAGE_EXTS` 需 `ocr_enabled + run_real_ocr + PaddleOCR` | 无 OCR 时 `content=""` + `ocr_pending=True` 空占位（PB-1）；有 OCR 时仅抽**纯文本扁平串** |
| **版面复杂** | 资料有结构（多栏、成分/营养表、图表、公式、阅读顺序） | 当前仅 PP-OCRv6 文本 OCR + GapTree 排版；`image_table.py` 的 `tables=[]` 恒空；PP-StructureV3 已 disabled | 表格退化为纯文本行、图表/公式**完全丢失**、阅读顺序靠启发式猜测（D4） |

> 一句话：当前 Tier 只解决"图上有什么字"，没解决"字的版面结构是什么"。
> 而母婴垂类的核心资料（奶粉配料表、营养成分表、原料规格表）恰恰是**版面复杂**的重灾区。

---

## 2. 调研结论：两个 PaddleOCR 新模型如何补位

### 2.1 PP-OCRv6（通用文字识别，**文本层 Tier**，当前已在用）

- **定位**：PP-OCR 第六代通用文本检测+识别，基于 **PPLCNetV4** 统一骨干。
- **模型族**：三档参数量 **1.5M（tiny）/ 更小（small）/ 34.5M（medium）**，覆盖 1.5M–34.5M。
- **精度**：medium 在自建多场景基准上比 PP-OCRv5_server 检测 Hmean +4.6%、识别精度 +5.1%，**2.37× GPU 加速**；34.5M 参数量即超越 Qwen3-VL-235B / GPT-5.5 在 OCR 精度上。
- **语言**：单模型 50 语言（tiny 49，不含日语）；中/英/日/46 种拉丁语系统一。
- **速度（端侧友好）**：medium A100 0.29s/图；**tiny 在 Apple M4 仅 0.96s、Intel Xeon OpenVINO 0.20s**；纯 CPU 可跑。
- **部署**：Windows/Linux/Mac + NVIDIA GPU/Intel CPU/昆仑/昇腾；ONNX Runtime、OpenVINO、TensorRT、HPI 插件；支持自定义训练/微调。
- **关键优势**：**仅纯文本 OCR，CPU 可跑，无 GPU 依赖，模型小（~30MB）** —— 这正是当前端侧门店机器能用的"保底 Tier"。
- **关键局限**：**不做版面分析、不输出表格/图表/公式结构、不还原阅读顺序**。对版面复杂资料只会吐一坨扁平文本。

### 2.2 PaddleOCR-VL（视觉语言文档解析，**版面/结构 Tier**，本次新增候选）

- **定位**：百度飞桨面向文档解析的 SOTA 视觉语言模型，核心是 **PaddleOCR-VL-0.9B**（0.9B 参数的紧凑 VLM）。
- **架构（两段式）**：
  1. **版面分析 `PP-DocLayoutV2`**：RT-DETR 检测 + 6 层 pointer network，输出元素框 / 类别 / **阅读顺序**。
  2. **元素识别 `PaddleOCR-VL-0.9B`**：LLaVA 式（NaViT 动态高分辨率视觉编码器 + ERNIE-4.5-0.3B 语言模型），输出聚合为 **Markdown / JSON**。
- **能力覆盖（直接命中本板块缺口）**：
  - 文本：印刷/手写/竖排/多语/艺术字/emoji/生僻字；
  - **表格**：有框/无框、合并单元格、手写、发票，输出 **OTSL**；
  - **公式**：行内/独立，LaTeX；
  - **图表**：柱/线/饼/散点等 → **Markdown 表格**（内部 RMS-F1 0.844，超过 Qwen2.5-VL-72B 的 0.730）；
  - **阅读顺序**：原生输出，不再靠启发式。
- **多语**：**109 语言**（含阿拉伯、俄、印地、泰、日、韩等）。
- **精度（SOTA）**：OmniDocBench v1.5 总评 **92.56**（次优 MinerU2.5-1.2B 90.67）；olmOCR-Bench **80.0±1.0**；A100 上 1.224 页/秒（vLLM），比 dots.ocr 省 ~40% 显存。
- **License**：**Apache-2.0**（HuggingFace `PaddlePaddle/PaddleOCR-VL`）—— **可商用、可自托管、数据不出域**，与合规约束兼容。
- **部署形态（全部本地/自托管）**：
  - PaddleOCR CLI/Python：`paddleocr doc_parser -i <img> --pipeline_version v1`，或 `from paddleocr import PaddleOCRVL`；
  - **vLLM 服务端**（Docker，CUDA GPU）：`--vl_rec_backend vllm-server --vl_rec_server_url ...`；
  - HuggingFace `transformers`（元素级：ocr/table/chart/formula）；
  - 多线程异步流水线，单 A100 批量 512 页。
- **关键约束 / 注意点**：
  - **需要 GPU**（0.9B + bf16，现代 NVIDIA 卡；无明确最小显存数字，但非 CPU 友好）；
  - **图表识别默认关闭**，需显式 `use_chart_recognition=True` 开启；
  - 非标准文档（车牌/票证/证件）可关版面检测、直接 `PaddleOCR-VL-0.9B` 单模型；
  - **暂不支持微调**（官方高优先，将发布）；如需领域适配走 ERNIEKit SFT（路线图）。

### 2.3 两者不是替代，而是互补

| 维度 | PP-OCRv6（文本 Tier） | PaddleOCR-VL（版面/结构 Tier） |
|---|---|---|
| 解决什么 | "图上有什么字" | "字的版面结构是什么" |
| 表格/图表/公式 | ❌ | ✅（OTSL / Markdown / LaTeX） |
| 阅读顺序 | 启发式（GapTree） | ✅ 原生输出 |
| 硬件 | **CPU 可跑** | 需 GPU |
| 模型体积 / 下载 | ~30MB，离线可预置 | 0.9B，需较大模型包 |
| 合规 | 自托管 OK | Apache-2.0 自托管 OK |
| 定位 | 保底、端侧、快 | 重活、服务端、准 |

---

## 3. 目标架构：dataproc 图片解析三档 Tier

沿用现有 `dataproc` 哲学——**"引擎只做编排 + 诚实搬运；OCR/结构化抽取/LLM 为 Tier（可选装）"**。
新增「版面/结构 Tier」，与现有文本 OCR Tier、结构化 LLM Tier 并列：

```
资料（图片 / PDF / 扫描件）
        │
        ├─ Tier A  纯文本 OCR（已有，常驻）
        │      PP-OCRv6_small（CPU+mkldnn）
        │      → 扁平文本；端侧门店机器也能跑
        │
        ├─ Tier B  版面 / 结构解析（新增，可选装，需 GPU/自托管）
        │      PaddleOCR-VL-0.9B（PP-DocLayoutV2 + VLM，vLLM 服务端）
        │      → Markdown/JSON：文本+表格(OTSL)+图表+公式+阅读顺序
        │      → 命中即产出结构化 corpus（替代被禁的 PP-StructureV3）
        │
        └─ Tier C  人工待确认（兜底，永远在线）
              当 Tier A/B 不可用 / 离线 / 置信度低
              → ocr_pending + 操作员在 GUI 预览核对 / 转 Excel 结构化录入
              → 闭环回写（pending-confirmation loop）
```

**派发规则（诚实搬运，不编造）**：
1. 端侧无 GPU / `ocr_enabled=False` / 离线 → 只跑 Tier A；版面复杂图退化为扁平文本并标 `ocr_pending`。
2. 检测到版面/表格/图表特征（或所在文件夹为「原料资料/产品资料」且为图片）→ 若 Tier B 已装配，则走 Tier B 出结构化 Markdown。
3. Tier B 输出再喂给现有 `structurer`（`structure()` + `classify()` + `resolve()`）做产品结构化抽取，与 `.md` 产品资料同一条链路。
4. Tier B 不可用 / 解析失败 → 回退 Tier A 文本 + `ocr_pending=True`，进入 Tier C 人工闭环（**绝不编造表格内容**）。

---

## 4. 与既有约束 / 缺陷的对应

| 既有缺口 | 本板块如何闭合 |
|---|---|
| **PB-1** 图片无 OCR → 空占位 | Tier A 常驻 + Tier B 可选；两者皆不可用时仍走 Tier C `ocr_pending` + 可见 WARN（已由 PB-1 修复补 WARN，此处补"能解析"的另一半） |
| **D4** PP-Structure 表格禁用 → 成分表结构丢失 | **Tier B（PaddleOCR-VL）直接替代 PP-StructureV3**：一次性解决表格+图表+公式+阅读顺序，且比原 server 模型快得多（GPU 1.2 页/秒 vs 旧 >2 分钟/大图） |
| **D6** 离线端侧首次联网下载 ~300MB | Tier A 模型（~30MB）纳入安装包 Tier 2 捆绑；Tier B 模型（0.9B）预置到高配/HQ 服务器镜像，门店端侧默认只启用 Tier A，规避联网依赖 |
| **D11** GUI 仅预览 .md/.txt | Tier B 输出 Markdown → GUI 可直接渲染预览/核对（前端 `<img>` + Markdown 对照），运营校验 OCR/解析结果不再盲导 |
| **合规不出域** | PP-OCRv6 / PaddleOCR-VL 均自托管（Apache-2.0）；图片不出门店/企业服务器，满足端侧 1 家 1 实例 + 数据不出域 |

---

## 5. 落地路径（分阶段，每步带回归）

### 阶段 0 — 现状固化（已完成）
- PP-OCRv6_small 文本 OCR 常驻（`_paddle_ocr.py`）；PB-1 的 `ocr_pending` WARN + manifest 计数已落地。

### 阶段 1 — 新增 Tier B 适配器（可选装，默认 OFF）
- 新增 `tools/dataproc/adapters/_paddle_ocr_vl.py`：
  - 单例 + vLLM 服务端懒加载（同 `_paddle_ocr.py` 的双重检查锁模式）；
  - `extract(path) -> DocResult(text: Markdown, tables, charts, formulas, reading_order)`；
  - `get_adapter(".png"/".jpg"/".pdf")` 增加"若 Tier B 已装配则路由到 VL"的分支。
- `config.py` 增加 `vl_enabled`（默认 `False`）、`vl_server_url`（空=本地 PaddleOCRVL，非空=远程 vLLM）。
- `build.py._process_nontext`：当 `cfg.vl_enabled` 且 Tier B 可用 → 走 VL 解析，产物为 Markdown 结构化文本；失败回退 Tier A/占位。
- **回归测试**（CVC 硬要求，红跑→绿跑）：
  - `test_dataproc_vl_tier.py`：① `vl_enabled=False` 时仍走旧路径、不崩；② `vl_enabled=True` 但服务不可达 → 回退 Tier A + `ocr_pending`；③ mock VL 返回 Markdown 时 corpus 含结构化表格文本、不再扁平。

### 阶段 2 — 图表/公式开关 + GUI 预览
- Tier B 默认 `use_chart_recognition=True`（母婴资料常见营养成分图）；公式按需。
- `dataproc/gui` 前端对 `ocr_pending` 资料展示原图 + 提取 Markdown 对照（D11）。

### 阶段 3 — 离线预置与安装包分层
- 安装包 Tier 2 捆绑 PP-OCRv6 模型；Tier B（0.9B）作为可选高配镜像预置，README 给出"无 GPU 门店只用 Tier A"的明确引导（闭合 D6）。

### 阶段 4（可选，未来）— 领域微调
- 待 PaddleOCR-VL 开放微调，用母婴标签/成分表样本 SFT，进一步抬升版面识别率。

---

## 6. 风险与未决项

1. **Tier B 需 GPU**：低配门店（无独显）只能走 Tier A + 人工闭环；服务端/HQ 高配机才启用 Tier B。这是产品决策而非技术阻塞。
2. **0.9B 显存占用**：未公布最小显存，需实机压测（预估 bf16 ~2GB+，量化版已有 llama.cpp/Ollama 适配可降本）。
3. **图表默认关**：务必显式开启，否则图表资料仍丢失。
4. **微调暂不支持**：领域适配靠通用 0.9B + 现有 `structurer` 规则兜底；SFT 待官方。
5. **vLLM 服务端运维**：门店端侧若启用 Tier B，要管一个本地推理服务（Docker），增加部署面——建议 Tier B 仅 HQ/高配门店，普通门店保持 Tier A。

---

## 7. 一句话结论

> 用 **PP-OCRv6（纯文本，CPU 保底）** 守端侧底线，用 **PaddleOCR-VL-0.9B（版面/表格/图表/公式/阅读顺序，GPU 自托管，Apache-2.0）** 补版面复杂资料的"结构"缺口，
> 二者经现有 `structurer` + `pending-confirmation` 闭环汇入同一 corpus 链路，直接闭合 **PB-1 / D4 / D6 / D11**，且不破坏合规不出域与端侧 1 家 1 实例约束。
