#!/usr/bin/env python3
# @module ingest
"""范式② 抽取层验收（PARADIGM2_EXTRACTION_DESIGN.md）。

  直接运行：python3 test_paradigm2.py  → 退出码 0 全过，非 0 有失败。
  覆盖：
    P2a  from_config 认 lmstudio（OpenAI 兼容，默认 base_url=http://localhost:1234/v1）
    P2b  from_config 回归（none→None / mock / cloud / openai / ollama）
    P2c  structure + MockProvider：描述字段被 LLM 补全（brand 空→填）
    P2d  权威字段冲突：rule.reg_number≠llm → 保留 rule，needs_review=True
    P2e  权威字段一致：rule.net_content==llm → 无冲突
    P2f  描述字段冲突：rule.brand≠llm → 保留 rule，needs_review=True
    P2g  provider=None → 纯规则兜底（rule-only），needs_review=False（回归）
    P2h  LLM 返回非 JSON → parse_failed=True，不崩（回归）
    P2i  build_bundle + MockProvider：冲突产品 status=needs_review，manifest 计数含 needs_review
"""
import os
import sys
import json
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from dataproc.config import LLMConfig
from dataproc.llms import from_config, OpenAICompatProvider, OllamaProvider, MockProvider, ToolLLMProvider
from dataproc.structurer import structure, _fuse, FIELD_KEYS
from dataproc.build import build_bundle, TOP_FOLDERS
from dataproc.repo import init_repo


def _canned_provider(json_str: str) -> MockProvider:
    return MockProvider(canned=json_str)


def main():
    fails = []

    # P2a：lmstudio 映射
    try:
        p = from_config(LLMConfig(kind="lmstudio", model="qwen2.5-7b"))
        assert isinstance(p, OpenAICompatProvider), f"应 OpenAICompatProvider，实际 {type(p)}"
        assert p.base == "http://localhost:1234/v1", f"base 应 LMStudio 预设，实际 {p.base}"
        # 自定义 base_url 应生效
        p2 = from_config(LLMConfig(kind="lmstudio", base_url="http://10.0.0.5:1234/v1", model="m"))
        assert p2.base == "http://10.0.0.5:1234/v1"
        print("[PASS] P2a (lmstudio 映射)")
    except AssertionError as e:
        fails.append(f"P2a: {e}")
    except Exception as e:
        fails.append(f"P2a: 异常 {type(e).__name__}: {e}")

    # P2b：from_config 回归
    try:
        assert from_config(LLMConfig(kind="none")) is None
        assert isinstance(from_config(LLMConfig(kind="mock")), MockProvider)
        assert isinstance(from_config(LLMConfig(kind="cloud", model="m")), OpenAICompatProvider)
        assert isinstance(from_config(LLMConfig(kind="openai", model="m")), OpenAICompatProvider)
        assert isinstance(from_config(LLMConfig(kind="ollama", model="m")), OllamaProvider)
        print("[PASS] P2b (from_config 回归)")
    except AssertionError as e:
        fails.append(f"P2b: {e}")
    except Exception as e:
        fails.append(f"P2b: 异常 {type(e).__name__}: {e}")

    # P2c：描述字段被 LLM 补全
    try:
        text = "爱他美 白金版 3段 800g 适合 1-3岁 国食注字 A123"
        llm_json = json.dumps({"brand": "爱他美", "name": "白金版", "stage": "3段",
                                "net_content": "800g", "age_range": "1-3岁",
                                "manufacturer": "达能", "reg_number": "国食注字 A123"})
        st = structure(text, _canned_provider(llm_json))
        assert st.needs_review is False, f"全一致应无冲突，实际 {st.needs_review}"
        assert st.fields["brand"] == "爱他美" and st.fields["manufacturer"] == "达能"
        assert st.provider_used not in ("rule-only", "rule-only(fallback)")
        print("[PASS] P2c (描述字段 LLM 补全)")
    except AssertionError as e:
        fails.append(f"P2c: {e}")
    except Exception as e:
        fails.append(f"P2c: 异常 {type(e).__name__}: {e}")

    # P2d：权威字段冲突（reg_number）
    try:
        text = "国食注字 A123"
        llm_json = json.dumps({"reg_number": "B999", "brand": "x"})
        st = structure(text, _canned_provider(llm_json))
        assert st.needs_review is True, "reg_number 冲突应 needs_review"
        assert st.fields["reg_number"] == "国食注字 A123", f"权威字段应保留规则值，实际 {st.fields['reg_number']}"
        print("[PASS] P2d (权威字段冲突→保留规则+needs_review)")
    except AssertionError as e:
        fails.append(f"P2d: {e}")
    except Exception as e:
        fails.append(f"P2d: 异常 {type(e).__name__}: {e}")

    # P2e：权威字段一致
    try:
        text = "800g 净含量"
        llm_json = json.dumps({"net_content": "800g"})
        st = structure(text, _canned_provider(llm_json))
        assert st.needs_review is False, "一致应无冲突"
        assert st.fields["net_content"] == "800g"
        print("[PASS] P2e (权威字段一致→无冲突)")
    except AssertionError as e:
        fails.append(f"P2e: {e}")
    except Exception as e:
        fails.append(f"P2e: 异常 {type(e).__name__}: {e}")

    # P2f：描述字段冲突（brand）
    try:
        text = "a2 奶粉"
        llm_json = json.dumps({"brand": "a2 Platinum", "name": "至初"})
        st = structure(text, _canned_provider(llm_json))
        assert st.needs_review is True, "brand 冲突应 needs_review"
        assert st.fields["brand"] == "a2", f"描述冲突应保留规则值(保守)，实际 {st.fields['brand']}"
        print("[PASS] P2f (描述字段冲突→保留规则+needs_review)")
    except AssertionError as e:
        fails.append(f"P2f: {e}")
    except Exception as e:
        fails.append(f"P2f: 异常 {type(e).__name__}: {e}")

    # P2g：provider=None 纯规则兜底
    try:
        st = structure("飞鹤 星飞帆 1段 700g", None)
        assert st.provider_used == "rule-only"
        assert st.needs_review is False
        assert st.fields["brand"] == "飞鹤"
        print("[PASS] P2g (rule-only 兜底)")
    except AssertionError as e:
        fails.append(f"P2g: {e}")
    except Exception as e:
        fails.append(f"P2g: 异常 {type(e).__name__}: {e}")

    # P2h：非 JSON → parse_failed
    try:
        st = structure("随便一段文字", _canned_provider("这不是json"))
        assert st.parse_failed is True
        assert st.fields  # 仍返回规则字段，不崩
        print("[PASS] P2h (非JSON→parse_failed 不崩)")
    except AssertionError as e:
        fails.append(f"P2h: {e}")
    except Exception as e:
        fails.append(f"P2h: 异常 {type(e).__name__}: {e}")

    # P2i：build_bundle 端到端（MockProvider + 图片内容）
    try:
        repo = tempfile.mkdtemp(prefix="p2_")
        init_repo(repo, "测试企业", namespace="b")
        # 放一张"图片"（png），内容由 MockProvider 决定结构化结果
        import PIL.Image as PILImage
        prod_dir = os.path.join(repo, TOP_FOLDERS[0])  # 产品资料
        os.makedirs(prod_dir, exist_ok=True)
        img = os.path.join(prod_dir, "prod1.png")
        PILImage.new("RGB", (100, 100), (255, 255, 255)).save(img)

        # 关键：让 OCR 适配器返回「有文字」内容 + MockProvider 制造冲突
        # 用 monkeypatch 让图片适配器产出一个带冲突的文本，并注入 conflict MockProvider
        # 通过环境变量把 LLM 设为 mock，并让 MockProvider 吐出与规则冲突的 JSON
        os.environ["DATAPROC_LLM_KIND"] = "mock"
        os.environ["DATAPROC_LLM_MODEL"] = "mock"
        # 图片分支需 ocr_enabled + run_real_ocr 为真才会调用 adapter（否则直接占位）
        os.environ["DATAPROC_OCR_ENABLED"] = "1"
        os.environ["RUN_REAL_OCR"] = "1"
        # build 在模块顶层 `from .adapters import get_adapter`，故必须 patch build 模块自身的引用
        import dataproc.build as build_mod
        class _StubRes:
            text = "飞鹤 国食注字 A123"   # 含品牌关键词 → 规则 brand=飞鹤，与 LLM 冲突
            meta = {"ocr": True}
        class _StubAdapter:
            def extract(self, path, run_real_ocr=False):
                return _StubRes()
        orig_get = build_mod.get_adapter
        build_mod.get_adapter = lambda ext: _StubAdapter()
        # MockProvider 吐冲突 JSON（brand 不同）
        os.environ["DATAPROC_LLM_MOCK_JSON"] = json.dumps(
            {"brand": "冲突品牌", "reg_number": "国食注字 A123"})
        # MockProvider 需读取该 env 才能吐出冲突 JSON —— 自定义一个 provider 传入
        class _ConflictMock(MockProvider):
            def complete(self, prompt, system=""):
                return os.environ.get("DATAPROC_LLM_MOCK_JSON", "{}")
        out = tempfile.mkdtemp(prefix="p2_out_")
        # build 内部 provider = from_config(cfg.llm) → mock 不吐冲突 JSON；
        # 故 monkeypatch from_config 注入 conflict provider
        orig_from = build_mod.from_config
        build_mod.from_config = lambda cfg: _ConflictMock()
        try:
            summary = build_bundle(repo, out, selection={"files": [os.path.relpath(img, repo)]})
        finally:
            build_mod.from_config = orig_from
            build_mod.get_adapter = orig_get
        # 验证：至少 1 个产品，且含 needs_review
        mani = summary["manifest"]
        nr = mani["counts"].get("needs_review", 0)
        # 由于 stub adapter 文本 = 国食注字 A123，_mock 吐 brand=冲突品牌 → 描述冲突
        assert nr >= 1, f"应至少有 1 个 needs_review 产品，实际 {nr}"
        # 产品记录 status
        prod_path = os.path.join(out, "products.ndjson")
        with open(prod_path, encoding="utf-8") as f:
            prods = [json.loads(l) for l in f if l.strip()]
        assert any(p.get("status") == "needs_review" for p in prods), "应有产品 status=needs_review"
        print("[PASS] P2i (build 冲突产品→needs_review + manifest 计数)")
    except AssertionError as e:
        fails.append(f"P2i: {e}")
    except Exception as e:
        import traceback; traceback.print_exc()
        fails.append(f"P2i: 异常 {type(e).__name__}: {e}")

    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL GREEN (范式②抽取层：P2a lmstudio / P2b 回归 / P2c-f 融合 / P2g-h 兜底 / P2i 端到端)")
    sys.exit(0)


if __name__ == "__main__":
    main()
