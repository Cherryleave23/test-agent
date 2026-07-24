#!/usr/bin/env python3
# @module dataproc
"""营养品数据结构 + 可配置 schema 验收（NS1–NS7，CVC P5 真实可执行）。

  NS1  NutritionProduct 含 active_ingredients/dosage；to_chunks 产出「功效成分」「食用量」块
  NS2  structure(text, Mock, category="营养品") 产出 NUTRITION_KEYS 全字段，非奶粉字段集
  NS3  structure(text, Mock, category="配方粉") 仍走 MILK_KEYS（stage/reg_number），零退化
  NS4  _rule_extract(营养品文本, "营养品") 规则抽出 health_license/audience/efficacy/active_ingredients/dosage
  NS5  _fuse 权威字段按 category：营养品 health_license 冲突→保留规则+needs_review；milk reg_number 同理
  NS6  load_schemas 加载自定义类目 probiotic（extends nutrition+strain/cfu）；classifier 用 keywords 识别；
        structure/_rule_extract 在该类目下抽 strain/cfu
  NS7  WEBUI GET 返回 schema；POST 写入 conf.yaml；再次 GET 反映；其他段(product_categories)未被破坏

直接运行：python3 test_nutrition_schema.py  → 退出码 0 全过，非 0 有失败。
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

# NS6/NS7 共用一个隔离 conf.yaml（通过环境变量覆盖默认位置，避免污染仓库）
_CONF = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False).name
os.environ["DATAPROC_CONF_PATH"] = _CONF

from dataproc.structurer import structure, _rule_extract, _fuse, MILK_KEYS, NUTRITION_KEYS
from dataproc.schema_conf import load_schemas, schema_keys, auth_fields_for
from dataproc.classifier import classify
from dataproc.llms import MockProvider
from src.kb.models import NutritionProduct


def main():
    fails = []

    # ---------- NS1：模型补全 ----------
    try:
        n = NutritionProduct(
            name="X", brand="Y", category="益生菌", audience="婴幼儿", dosage_form="滴剂",
            age_range="0-3岁", price=99.0, origin="中国", manufacturer="Z",
            health_license="国食健字 G1", efficacy="调节肠道",
            ingredients="益生菌", nutrition="", highlights="", cautions="",
            active_ingredients="每滴含益生菌 100亿CFU", dosage="每日1-2滴",
        )
        assert hasattr(n, "active_ingredients") and hasattr(n, "dosage")
        titles = [c[0] for c in n.to_chunks()]
        assert "功效成分" in titles, f"to_chunks 缺「功效成分」块：{titles}"
        assert "食用量" in titles, f"to_chunks 缺「食用量」块：{titles}"
        assert "每滴含益生菌 100亿CFU" in n.to_search_text()
        assert "每日1-2滴" in n.to_search_text()
        print("[PASS] NS1 (NutritionProduct 含 active_ingredients/dosage + 分块检索)")
    except AssertionError as e:
        fails.append(f"NS1: {e}")
    except Exception as e:
        fails.append(f"NS1: 异常 {type(e).__name__}: {e}")

    # ---------- NS2：营养品分类感知抽取（LLM 路径） ----------
    try:
        nut_text = ("宝宝益生菌滴剂 适宜人群婴幼儿 保健功能调节肠道菌群 "
                    "每滴含益生菌 100亿CFU 每日1-2滴 国食健字 G2023")
        nut_json = json.dumps({
            "name": "宝宝益生菌滴剂", "brand": "Y", "category": "益生菌", "audience": "婴幼儿",
            "dosage_form": "滴剂", "age_range": "0-3岁", "price": 99.0, "origin": "中国",
            "manufacturer": "Z", "health_license": "国食健字 G2023", "efficacy": "调节肠道菌群",
            "active_ingredients": "每滴含益生菌 100亿CFU", "dosage": "每日1-2滴",
            "ingredients": "益生菌", "nutrition": "", "highlights": "", "cautions": "", "keywords": "",
        })
        st = structure(nut_text, MockProvider(nut_json), category="营养品")
        for k in NUTRITION_KEYS:
            assert k in st.fields, f"营养品字段缺失：{k}"
        for must in ("active_ingredients", "dosage", "efficacy", "audience", "health_license",
                     "category", "dosage_form"):
            assert st.fields.get(must), f"营养品关键字段为空：{must}"
        assert "milk_origin" not in st.fields, "营养品不应含 milk_origin（误用奶粉字段集）"
        print(f"[PASS] NS2 (营养品抽取 NUTRITION_KEYS 全字段，非奶粉字段集，{len(NUTRITION_KEYS)} 键)")
    except AssertionError as e:
        fails.append(f"NS2: {e}")
    except Exception as e:
        fails.append(f"NS2: 异常 {type(e).__name__}: {e}")

    # ---------- NS3：奶粉路径零退化 ----------
    try:
        milk_text = "飞鹤 星飞帆 1段 700g 国食注字 A123"
        milk_json = json.dumps({
            "name": "星飞帆", "brand": "飞鹤", "stage": "1段", "age_range": "0-6个月",
            "net_content": "700g", "price": 300.0, "origin": "中国", "milk_origin": "黑龙江",
            "ptype": "牛奶粉", "reg_number": "国食注字 A123", "manufacturer": "飞鹤",
            "ingredients": "生牛乳", "nutrition": "DHA", "highlights": "易吸收", "keywords": "",
        })
        st = structure(milk_text, MockProvider(milk_json), category="配方粉")
        assert st.fields.get("stage") == "1段", f"stage 退化：{st.fields.get('stage')}"
        assert st.fields.get("reg_number") == "国食注字 A123", f"reg_number 退化：{st.fields.get('reg_number')}"
        assert "milk_origin" in st.fields, "奶粉字段集应含 milk_origin"
        assert "health_license" not in st.fields, "奶粉不应含 health_license"
        print("[PASS] NS3 (奶粉路径仍走 MILK_KEYS，stage/reg_number 零退化)")
    except AssertionError as e:
        fails.append(f"NS3: {e}")
    except Exception as e:
        fails.append(f"NS3: 异常 {type(e).__name__}: {e}")

    # ---------- NS4：营养品规则抽取 ----------
    try:
        text = ("某营养品\n适宜人群：婴幼儿\n保健功能：增强免疫力\n"
                "每粒含 DHA 100mg、钙 300mg\n每日2粒，温水送服\n国食健字 G2023")
        rule = _rule_extract(text, "营养品")
        for must in ("health_license", "audience", "efficacy", "active_ingredients", "dosage"):
            assert rule.get(must), f"规则未抽出 {must}：{rule.get(must)!r}"
        assert rule["health_license"] == "国食健字 G2023"
        assert rule["audience"] == "婴幼儿"
        assert rule["dosage"] == "每日2粒"
        print(f"[PASS] NS4 (规则抽出营养品 health_license/audience/efficacy/active_ingredients/dosage)")
    except AssertionError as e:
        fails.append(f"NS4: {e}")
    except Exception as e:
        fails.append(f"NS4: 异常 {type(e).__name__}: {e}")

    # ---------- NS5：权威字段按 category 冲突 → needs_review ----------
    try:
        # 营养品：health_license 冲突
        nut_text = "国食健字 G1"
        nut_json = json.dumps({"health_license": "国食健字 G2", "name": "x"})
        stn = structure(nut_text, MockProvider(nut_json), category="营养品")
        assert stn.needs_review is True, "营养品 health_license 冲突应 needs_review"
        assert stn.fields["health_license"] == "国食健字 G1", "冲突应保留规则值"
        # 奶粉：reg_number 冲突
        milk_text = "国食注字 A123"
        milk_json = json.dumps({"reg_number": "B999", "brand": "x"})
        stm = structure(milk_text, MockProvider(milk_json), category="配方粉")
        assert stm.needs_review is True, "奶粉 reg_number 冲突应 needs_review"
        assert stm.fields["reg_number"] == "国食注字 A123", "冲突应保留规则值"
        # 非权威描述字段冲突：保守保留规则值 + needs_review
        assert "health_license" in auth_fields_for("营养品")
        assert "reg_number" in auth_fields_for("配方粉")
        assert "milk_origin" not in auth_fields_for("营养品")
        print("[PASS] NS5 (权威字段按 category 冲突→保留规则值+needs_review)")
    except AssertionError as e:
        fails.append(f"NS5: {e}")
    except Exception as e:
        fails.append(f"NS5: 异常 {type(e).__name__}: {e}")

    # ---------- NS6：自定义类目 probiotic（extends nutrition + strain/cfu） ----------
    try:
        # 写自定义类目到隔离 conf
        from dataproc.schema_conf import write_product_schemas
        write_product_schemas({
            "probiotic": {
                "label": "益生菌", "kind": "nutrition", "extends": "nutrition",
                "keywords": ["益生菌", "probiotic", "菌群"],
                "fields": [
                    {"key": "strain", "label": "菌株", "type": "text"},
                    {"key": "cfu", "label": "活菌数(CFU)", "type": "text"},
                ],
            }
        })
        schemas = load_schemas()
        assert "probiotic" in schemas, "probiotic 未加载"
        pkeys = [f["key"] for f in schemas["probiotic"]["fields"]]
        assert "strain" in pkeys and "cfu" in pkeys, f"probiotic 缺 strain/cfu：{pkeys}"
        assert schemas["probiotic"]["kind"] == "nutrition"
        # classifier 用 keywords 识别
        text = "某益生菌滴剂\n菌株：BB-12\n活菌数(CFU)：100亿\n每日1滴"
        cls = classify(text)
        assert cls["product_category"] == "probiotic", f"classifier 应识别 probiotic，实际 {cls}"
        assert cls["kind"] == "nutrition"
        # 规则抽取 strain/cfu（label 匹配）
        rule = _rule_extract(text, "probiotic")
        assert rule.get("strain") == "BB-12", f"strain 未抽出：{rule.get('strain')!r}"
        assert rule.get("cfu") == "100亿", f"cfu 未抽出：{rule.get('cfu')!r}"
        # structure 在该类目下抽 strain/cfu
        pjson = json.dumps({
            "name": "益生菌滴剂", "brand": "某", "category": "益生菌", "audience": "婴幼儿",
            "dosage_form": "滴剂", "age_range": "0-3岁", "price": 99.0, "origin": "中国",
            "manufacturer": "Z", "health_license": "国食健字 G1", "efficacy": "调节肠道",
            "active_ingredients": "每滴含益生菌", "dosage": "每日1滴",
            "ingredients": "益生菌", "nutrition": "", "highlights": "", "cautions": "",
            "strain": "BB-12", "cfu": "100亿", "keywords": "",
        })
        stp = structure(text, MockProvider(pjson), category="probiotic")
        assert stp.fields.get("strain") == "BB-12", f"structure strain 未抽出：{stp.fields.get('strain')!r}"
        assert stp.fields.get("cfu") == "100亿", f"structure cfu 未抽出：{stp.fields.get('cfu')!r}"
        print("[PASS] NS6 (自定义类目 probiotic：load_schemas/classifier/抽取 全链路)")
    except AssertionError as e:
        fails.append(f"NS6: {e}")
    except Exception as e:
        fails.append(f"NS6: 异常 {type(e).__name__}: {e}")

    # ---------- NS7：WEBUI schema 端点读写 conf.yaml ----------
    try:
        import yaml
        # 预置 product_categories 段，验证不被 POST 破坏
        with open(_CONF, "w", encoding="utf-8") as f:
            yaml.safe_dump({"product_categories": {"牛奶粉": "配方粉", "DHA": "营养品"}}, f, allow_unicode=True)
        from fastapi.testclient import TestClient
        from dataproc.gui.backend.main import app
        c = TestClient(app)
        g1 = c.get("/settings/schema")
        assert g1.status_code == 200, f"GET /settings/schema 失败：{g1.status_code}"
        assert "milk" in g1.json()["schemas"] and "nutrition" in g1.json()["schemas"]
        body = {"schemas": {"probiotic": {
            "label": "益生菌", "kind": "nutrition", "extends": "nutrition",
            "keywords": ["益生菌", "probiotic", "菌群"],
            "fields": [
                {"key": "strain", "label": "菌株", "type": "text"},
                {"key": "cfu", "label": "活菌数(CFU)", "type": "text"},
            ],
        }}}
        p1 = c.post("/settings/schema", json=body)
        assert p1.status_code == 200, f"POST 失败：{p1.status_code} {p1.text}"
        assert "probiotic" in p1.json()["schemas"], "POST 后未反映 probiotic"
        assert p1.json()["schemas"]["probiotic"]["builtin"] is False
        g2 = c.get("/settings/schema")
        assert "probiotic" in g2.json()["schemas"], "再次 GET 未反映 probiotic"
        # 校验 conf.yaml 其他段未被破坏
        with open(_CONF, encoding="utf-8") as f:
            conf = yaml.safe_load(f)
        assert "product_categories" in conf, "POST 破坏了 product_categories 段"
        assert conf.get("product_categories") == {"牛奶粉": "配方粉", "DHA": "营养品"}
        assert "product_schemas" in conf and "probiotic" in conf["product_schemas"]
        print("[PASS] NS7 (WEBUI GET/POST schema 落盘 conf.yaml，其他段保留)")
    except AssertionError as e:
        fails.append(f"NS7: {e}")
    except Exception as e:
        fails.append(f"NS7: 异常 {type(e).__name__}: {e}")

    # 清理临时 conf
    try:
        os.remove(_CONF)
    except OSError:
        pass

    print()
    if fails:
        print("RESULT: FAIL (%d)" % len(fails))
        for f in fails:
            print("  - " + f)
        sys.exit(1)
    print("RESULT: ALL GREEN (营养品数据结构 + 可配置 schema：NS1-NS7)")


if __name__ == "__main__":
    main()
