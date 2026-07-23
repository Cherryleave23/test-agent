# OCR + LLM 开源项目行业盘点（2025–2026）

> 背景：为 TOB 母婴垂类 agent 的"知识转化"功能选型，目标是在**端侧、1 家 1 agent、多员工微信**环境下，把商品图/参数表可靠地转成结构化数据。
> 核心约束：**零捏造（合规优先）** > 识别聪明。

## 一、核心判断

业界**没有**开箱即用的"OCR+LLM 商品参数结构化抽取"成品。所有项目都在解决同一件事：把图变成可信结构化数据。按技术路线分三大家族。

## 二、三大技术范式

| 范式 | 思路 | 代表项目 | 对我们的适配度 |
|---|---|---|---|
| ① OCR 流水线 | 先 OCR 出文本框/表格 → 再喂 LLM 做字段抽取/补漏 | PaddleOCR 系列、MinerU、Marker、Docling | 主力候选（可控、可审计） |
| ② 版面 + VLM 两阶段 | 先版面分析定位区块 → VLM 逐块理解语义 | PP-StructureV3、olmOCR、LangExtract | **强推荐**（结构化质量最高、可溯源） |
| ③ VLM 端到端 | 一张图进 VLM 直接出结构化结果 | DeepSeek-OCR、HunyuanOCR-1B、GLM-OCR、PaddleOCR-VL | 端侧友好但**易幻觉** |

## 三、关键开源项目近况（数据截至 2026-07）

### OCR 流水线 / 版面两阶段
- **PaddleOCR**（百度）github.com/PaddlePaddle/PaddleOCR — 工业级标杆。2026-05 发布 **PaddleOCR-VL-1.6**（VLM 两阶段）；**PP-StructureV3** 做表格/版面；**PP-ChatOCR** 做 few-shot + LLM 场景化抽取。**最贴近我们已有技术栈**（已在用 PaddleOCR 3.x）。
- **olmOCR**（AllenAI，2025-07 v0.2.1）— LLM 把 PDF 线性化为 LLM-ready 文本，默认 FP8、快，自带 olmOCR-Bench。偏长文档→纯净文本，非商品参数表。
- **MinerU**（上海 AI 实验室）— 中文复杂版面解析强，偏学术文献/财报。
- **Marker / Docling**（IBM）— PDF→MD/JSON 成熟，通用文档管线。

### VLM 端到端（小模型、可端侧）
- **DeepSeek-OCR**（2025-10，3B）— 上下文光学压缩，高分辨率低显存，OmniDocBench SOTA；但 3B 需 GPU，纯 CPU 端侧吃力。
- **HunyuanOCR-1B**（腾讯，2025-11，仅 1B）— 端到端 OCR 专用 VLM，参数小、适合端侧。
- **GLM-OCR**（智谱，2026-02，0.9B）— 轻量多模态 OCR，复杂文档高精度，端侧潜力最大。
- **PaddleOCR-VL-1.6** — VLM 路线，中文复杂版面+公式一步到位。

### LLM 抽取框架（文本→结构化层）
- **Google LangExtract**（2025-07 开源，~20k★）— 把非结构化文本用 LLM 抽成**带溯源（source grounding）**的结构化数据，支持 **Gemini + Ollama 本地**。强制每个抽取字段回链原文片段，**天然抗幻觉**，理念最值得复用。
- **OCRLLM**（EasyOCR + Llama3.2-Vision / MiniCPM-V + Ollama）— OCR+本地多模态直接抽取的开源组合范例。

## 四、对母婴 TOB 场景的选型结论

端侧 1 家 1 agent、多员工微信、商品参数零捏造（合规），决定优先级：**可信 > 聪明**。

1. **主力路线＝范式②（OCR 流水线 + LLM 补全）**，不纯用范式③ VLM 端到端。VLM 端到端在商品参数表上**幻觉率高**（会把"未标注"字段编出来），母婴商品错一个字即合规事故。OCR 先锚定白纸黑字的事实，LLM 只补 OCR 看不清的语义（配料、卖点），并强制字段回链原文。
2. **端侧 VLM** 若采用，优先 HunyuanOCR-1B / GLM-OCR-0.9B（1B 级可 CPU/低端 GPU）；DeepSeek-OCR 3B 需 GPU，适合有卡企业。
3. **抽取层借鉴 LangExtract 的 source grounding**：每个结构化字段带原文坐标/片段，前端可点字段跳转到图上位置——正好接现有 OCR 坐标体系（tbpu 已恢复阅读顺序）。
4. **我们已在正确轨道**：PaddleOCR 3.x + tbpu 阅读顺序恢复 + rule-only 抽取，只差"LLM 补全层"和"可信度标记"。下一步把 LLM 设置页做出来，让企业接 LMStudio（本地）/ 云端，跑范式②。

## 五、与我们现有实现的映射

| 我们已有 | 对应行业范式 | 缺失 |
|---|---|---|
| PaddleOCR 3.x OCR（`_paddle_ocr.py`） | ① 流水线 OCR 引擎 | — |
| 三级图像路由 + Scheme1 超大图（`image_table.py`） | ① 工程化预处理 | — |
| tbpu 阅读顺序恢复 | ① 版面/顺序 | — |
| rule-only 抽取（`structure()`/`classify()`） | 字段解析（无 LLM） | brand/name 等常为空 |
| `DataprocConfig.llm`（kind=none） | — | **LLM 补全层未接** |
| 无 source grounding | ② LangExtract 思想 | 字段→原文回链未做 |

> 下一步（用户已确认方向）：在 dataproc WebUI 单独做 LLM 设置页，支持本地 LMStudio（OpenAI 兼容 `http://localhost:1234/v1`）与云端 LLM，复用 agent 的 WebUI 设置板块逻辑。
