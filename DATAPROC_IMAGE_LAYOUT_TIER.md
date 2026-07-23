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

### 2.2 PaddleOCR-VL（视觉语言文档解析，**版面/结构 Tier**）

- **演进**：初代 PaddleOCR-VL（arXiv 2510.14528，OmniDocBench v1.5 ≈ 92.56%）→ **1.5**（异形框定位）→ **1.6（2026-06 发布，本次采用）**。1.6 在 1.5 基础上用"模型驱动数据引擎 + 渐进式后训练（继续预训练→SFT→RL）"定向修补欠优化区域，**架构不变、零成本换模型升级**。
- **定位**：百度飞桨面向文档解析的 SOTA 视觉语言模型，核心是 **PaddleOCR-VL-0.9B**（0.9B 参数的紧凑 VLM，沿用至 1.6）。
- **v1.6 关键指标**：
  - **OmniDocBench v1.6 总评 96.33%（新 SOTA）**，全面领先开源/闭源通用大模型与专用 OCR；
  - **Real5-OmniDocBench（扫描/弯折/屏摄/光照/倾斜 5 真实扰动场景）93.19% SOTA**，领先 Gemini-3 Pro 近 4 分；
  - **语言 111 种**（较初代 109 再扩）；
  - 较 1.5 在文本/公式/表格识别全面提升，并**大幅增强复杂表格、古籍、生僻字、图表、印章、spotting、文字检测识别**。
- **能力覆盖（直接命中本板块缺口）**：文本（印刷/手写/竖排/多语/艺术字/生僻字）；**表格 OTSL**；**公式 LaTeX**；**图表→Markdown**；**阅读顺序原生**；v1.6 新增**印章 / spotting**。
- **架构（两段式）**：`PP-DocLayoutV2` 版面（含阅读顺序）+ `PaddleOCR-VL-0.9B` 元素识别 → Markdown/JSON。
- **License**：**Apache-2.0**（HuggingFace / ModelScope `PaddlePaddle/PaddleOCR-VL-1.6`）—— 可商用、可自托管、数据不出域。
- **部署形态（全部本地/自托管）**：
  - PaddleOCR CLI/Python：`paddleocr doc_parser -i <img> --pipeline_version v1.6`，或 `from paddleocr import PaddleOCRVL; PaddleOCRVL(pipeline_version="v1.6")`；
  - **vLLM 服务端**（Docker，CUDA GPU）；
  - HuggingFace `transformers>=5`（元素级：ocr/table/chart/formula/seal/spotting）；
  - 多线程异步流水线，A100 1.224 页/秒（vLLM），比 dots.ocr 省 ~40% 显存；PaddlePaddle 框架需 ≥ 3.2.1。
- **微调（v1.6 新增，修正此前"不支持"结论）**：`ms-swift` 已支持 PaddleOCR-VL-1.6 的 **LoRA SFT + RL**，母婴标签/成分表领域适配成为可能（此前 1.0/1.5 不支持微调）。
- **关键约束 / 注意点**：
  - **需要 GPU**（0.9B + bf16，现代 NVIDIA 卡；非 CPU 友好）；
  - **图表识别默认关闭**，需显式 `use_chart_recognition=True` 开启；
  - 非标准文档（车牌/票证/证件）可关版面检测、直接 `PaddleOCR-VL-0.9B` 单模型；
  - 作为**生成式 VLM**，仍继承一定"生成式幻觉"风险（虽比通用 VLM 忠实得多），合规敏感场景需配合 PP-OCRv6 文本交叉校验 + pending 待确认。

### 2.3 两者不是替代，而是互补

| 维度 | PP-OCRv6（文本 Tier） | PaddleOCR-VL-1.6（版面/结构 Tier） |
|---|---|---|
| 解决什么 | "图上有什么字"（忠实转录） | "字的版面结构是什么"（结构化） |
| 表格/图表/公式/印章 | ❌ | ✅（OTSL / Markdown / LaTeX / seal） |
| 阅读顺序 | 启发式（GapTree） | ✅ 原生输出 |
| 硬件 | **CPU 可跑**（tiny 0.13–0.96s/图） | 需 GPU（A100 1.224 页/秒） |
| 模型体积 / 下载 | 1.5M–34.5M，~30MB，离线可预置 | 0.9B，需较大模型包（vLLM 服务端/本地） |
| 语言 | 50（单模型统一） | **111** |
| 精度基准 | 检测 Hmean 86.2% / 识别 83.2%（自建多场景）；文本 OCR 超越 Qwen3-VL-235B、GPT-5.5 | **OmniDocBench v1.6 96.33%**、Real5 93.19%（文档级 SOTA） |
| 幻觉风险 | **极低**（CTC/NRTR 确定性转录，所见即所得） | 有（生成式 VLM，但比通用 VLM 忠实得多） |
| 微调 | 支持自定义训练/字典扩展 | **v1.6 起支持 LoRA SFT+RL（ms-swift）** |
| 合规 | 自托管 OK | Apache-2.0 自托管 OK |
| 定位 | 保底、端侧、快、零幻觉 | 重活、服务端、准、结构化 |

---

### 2.4 PP-OCRv6 其他部分（补全：上次未展开的维度）

除"模型族/精度/速度/语言/部署"外，PP-OCRv6 还有几个对母婴垂类部署**直接 relevant** 的要点，弥补"只看精度表"的盲区：

1. **三档硬件定位（与端侧约束强相关）**
   - `tiny`（1.5M）→ **边缘/IoT**；`small`（~9.6MB 检测 + 20.4MB 识别）→ **移动端/桌面**；`medium`（34.5M）→ **服务端**。
   - 这意味着同一套 PP-OCRv6 代码可下探到门店低配机（tiny/small/medium，CPU 即可，medium 更慢约 50s/图），上探到 HQ 服务器（medium，GPU 加速 2.37×），**无需换引擎**。

2. **零幻觉转录（合规敏感资料的关键优势）**
   - 官方实测对比：PP-OCRv6 **忠实复现图面文字**；而 Gemini-3.1-Pro / GPT-5.5 / Qwen3-VL-235B 等通用 VLM 会基于语言先验**产生"幻觉性纠错"**（把看到的字改成"它认为对的字"）。
   - 量化证据：文本**检测** Hmean——PP-OCRv6_medium **86.2%**，Gemini-3.1-Pro 46.8%、GPT-5.5 45.6%、Qwen3-VL-235B 38.3%。大 VLM 连"字在哪"都漏掉大半，更别说忠实。
   - 对母婴资料（成分表/营养标签/注册号）而言，"不编造、不漏识"优先级高于"结构漂亮"——这是 PP-OCRv6 作为 Tier A 兜底的硬理由。

3. **真实场景泛化（通用 VLM 传统弱区）**
   - 单模型覆盖 50 语言 + 多种工业难例：**数码屏显、点阵字符、轮胎印、艺术字、旋转/透视、针式打印**等。
   - 这些恰好是门店真实拍图场景（屏摄价签、点阵小票、瓶身喷码），PP-OCRv6 明显优于通用 VLM。

4. **工程化部署完整性**
   - **多 OS**（Windows/Linux/macOS）+ **多硬件**（NVIDIA GPU、Intel CPU、昆仑、昇腾）；
   - **HPI 高性能推理插件**（ONNX Runtime 后端，`enable_hpi=True`）+ **Serving 服务化部署**；
   - **transformers 引擎**支持（`transformers>=5.8.0`）；
   - **支持自定义训练 / 字典扩展 / 模型微调**（文本检测、识别均可）。

5. **架构创新（为何小模型能干翻大模型）**
   - 骨干 **PPLCNetV4**（MetaFormer 式 + 结构重参数化）；检测颈 **RepLKFPN**（大核 7×7、参数比 v5 少 31%）；识别颈 **EncoderWithLightSVTR**（局部卷积 + 全局注意力 + 加性残差）。
   - 正是这些让 34.5M 的 PP-OCRv6_medium 在纯文本 OCR 上**反超 235B 级 VLM**。

### 2.5 直接对比：PaddleOCR-VL v1.6 vs PP-OCRv6（两种解析路线的本质区别）

> "直接上 PaddleOCR-VL v1.6" 与 "继续用 PP-OCRv6" 不是同一件事——前者是**文档结构解析**，后者是**纯文本转录**。两者在能力、成本、风险三个轴上各擅胜场。

| 轴 | PP-OCRv6（文本路线） | 直接上 PaddleOCR-VL v1.6（VL 路线） |
|---|---|---|
| **输出本质** | 扁平文本串（"图上有什么字"） | 结构化 Markdown/JSON（"字 + 版面 + 表格 + 图表 + 公式 + 顺序"） |
| **版面复杂资料** | 表格退化为行文本、图表/公式**完全丢失** | 原生保留结构，直接可用 |
| **纯文本/简单图** | **更快更便宜、零幻觉、CPU 即可** | 杀鸡用牛刀，且生成式有低概率错字/幻觉 |
| **硬件门槛** | CPU 可跑（tiny 0.13–0.96s） | 需 GPU（0.9B，A100 1.224 页/秒） |
| **语言** | 50 | 111 |
| **精度定位** | 文本 OCR 超越 235B VLM | 文档级 96.33% SOTA（含结构） |
| **幻觉/合规** | 极低（确定性转录） | 有生成式风险，需交叉校验 + pending |
| **微调** | 支持自定义训练 | v1.6 起 LoRA SFT+RL（ms-swift） |
| **离线/端侧** | ✅ 模型小、易预置 | ⚠️ 0.9B 需较大包，宜服务端/HQ |

**结论性判断（落在三档 Tier 上）**：
- **不是"二选一"，而是"分层用"**：PP-OCRv6 是端侧保底与零幻觉基线；PaddleOCR-VL v1.6 是服务端的结构化重活。
- **直接只用 PaddleOCR-VL v1.6 的代价**：① 丢掉 PP-OCRv6 的零幻觉文本基线（VL 偶发错字/结构幻觉，母婴合规敏感）；② 端侧无 GPU 门店跑不动；③ 简单纯文本图也被迫走重模型，浪费算力。
- **直接只用 PP-OCRv6 的代价**：版面复杂资料（成分表/营养标签/图表）结构尽失，回到 PB-1/D4 老路。
- 因此沿用 §3 的三档设计：Tier A = PP-OCRv6（常驻、端侧、零幻觉），Tier B = PaddleOCR-VL v1.6（可选装、服务端、结构化），Tier C = 人工 pending 兜底；并新增**LLM 友好校验点**——Tier B 输出与 Tier A 文本做一致性比对，差异过大则标 `ocr_pending` 进人工闭环，把"VL 的生成式风险"关进合规笼子。

---

## 3. 目标架构：dataproc 图片解析三档 Tier

沿用现有 `dataproc` 哲学——**"引擎只做编排 + 诚实搬运；OCR/结构化抽取/LLM 为 Tier（可选装）"**。
新增「版面/结构 Tier」，与现有文本 OCR Tier、结构化 LLM Tier 并列：

```
资料（图片 / PDF / 扫描件）
        │
        ├─ Tier A  纯文本 OCR（已有，常驻）
        │      PP-OCRv6_medium（CPU+mkldnn，或 DATAPROC_OCR_DEVICE=gpu）
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
5. **v1.6 一致性校验（合规笼子）**：当 Tier B 与 Tier A 同时可用，比对 Tier B 结构化文本与 Tier A 扁平文本；若关键字段（注册号/含量数字/成分名）差异超阈值或 Tier B 出现 Tier A 未见过的实体，标 `ocr_pending` + `vl_consistency="review"`，进 Tier C 人工核对——把生成式 VLM 的低概率错字/结构幻觉拦在入库前。

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
- PP-OCRv6_medium 文本 OCR 常驻（`_paddle_ocr.py`）；PB-1 的 `ocr_pending` WARN + manifest 计数已落地。

### 阶段 1 — 新增 Tier B 适配器（可选装，默认 OFF）
- 新增 `tools/dataproc/adapters/_paddle_ocr_vl.py`：
  - 单例 + vLLM 服务端懒加载（同 `_paddle_ocr.py` 的双重检查锁模式）；
  - `extract(path) -> DocResult(text: Markdown, tables, charts, formulas, reading_order)`；
  - `get_adapter(".png"/".jpg"/".pdf")` 增加"若 Tier B 已装配则路由到 VL"的分支。
- `config.py` 增加 `vl_enabled`（默认 `False`）、`vl_server_url`（空=本地 PaddleOCRVL，非空=远程 vLLM）。
- `build.py._process_nontext`：当 `cfg.vl_enabled` 且 Tier B 可用 → 走 VL 解析，产物为 Markdown 结构化文本；失败回退 Tier A/占位。
- **回归测试**（CVC 硬要求，红跑→绿跑）：
  - `test_dataproc_vl_tier.py`：① `vl_enabled=False` 时仍走旧路径、不崩；② `vl_enabled=True` 但服务不可达 → 回退 Tier A + `ocr_pending`；③ mock VL 返回 Markdown 时 corpus 含结构化表格文本、不再扁平；④ **一致性校验**：mock VL 产出 Tier A 未见的关键数字/注册号时，corpus 标 `ocr_pending` + `vl_consistency="review"`，不直通入库（合规笼子可测）。

### 阶段 2 — 图表/公式开关 + GUI 预览
- Tier B 默认 `use_chart_recognition=True`（母婴资料常见营养成分图）；公式按需。
- `dataproc/gui` 前端对 `ocr_pending` 资料展示原图 + 提取 Markdown 对照（D11）。

### 阶段 3 — 离线预置与安装包分层
- 安装包 Tier 2 捆绑 PP-OCRv6 模型；Tier B（0.9B）作为可选高配镜像预置，README 给出"无 GPU 门店只用 Tier A"的明确引导（闭合 D6）。

### 阶段 4（可选）— 领域微调（v1.6 已支持）
- PaddleOCR-VL-1.6 经 `ms-swift` 支持 LoRA SFT+RL；可用母婴标签/成分表样本微调，进一步抬升版面识别率；微调后仍保留 Tier A 零幻觉文本作一致性校验基线。

---

## 6. 风险与未决项

1. **Tier B 需 GPU**：低配门店（无独显）只能走 Tier A + 人工闭环；服务端/HQ 高配机才启用 Tier B。这是产品决策而非技术阻塞。
2. **0.9B 显存占用**：未公布最小显存，需实机压测（预估 bf16 ~2GB+，量化版已有 llama.cpp/Ollama 适配可降本）。
3. **图表默认关**：务必显式开启，否则图表资料仍丢失。
4. **领域微调（v1.6 已解锁）**：PaddleOCR-VL-1.6 经 `ms-swift` 支持 LoRA SFT+RL，可用母婴标签/成分表样本做领域适配；但仍建议保留 PP-OCRv6 零幻觉文本作校验基线，微调后仍需 pending 闭环。
5. **vLLM 服务端运维**：门店端侧若启用 Tier B，要管一个本地推理服务（Docker），增加部署面——建议 Tier B 仅 HQ/高配门店，普通门店保持 Tier A。

---

## 7. 一句话结论

> 用 **PP-OCRv6（纯文本，CPU 保底，零幻觉）** 守端侧底线，用 **PaddleOCR-VL-1.6（0.9B，版面/表格/图表/公式/印章/阅读顺序，GPU 自托管，Apache-2.0，OmniDocBench v1.6 96.33% SOTA）** 补版面复杂资料的"结构"缺口；
> 二者经现有 `structurer` + `pending-confirmation` 闭环汇入同一 corpus 链路，并以"Tier B 输出 vs Tier A 文本一致性校验"把 VL 生成式风险关进合规笼子，直接闭合 **PB-1 / D4 / D6 / D11**，且不破坏合规不出域与端侧 1 家 1 实例约束。
