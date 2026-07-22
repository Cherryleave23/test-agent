#!/usr/bin/env python3
# @module ingest
"""P3 结构化抽取 + 商品实体解析验收（RES1–RES7 核心）。

  RES1  规则抽取（段位/净含量/品牌/注册号）默认绿跑，锚定原文不编造
  RES2  LLM provider 接线（OpenAI 兼容）：mock 响应即合并字段，无需真网络
  RES3  未知文本不编造（字段全空）
  RES4  resolve：reg_number 优先 → uid=reg:
  RES5  resolve：无注册号 (brand,name,stage) 元组兜底 → uid=tuple:
  RES6  MockProvider 经 from_config(kind=mock) 可用
  RES7  LLM JSON 解析失败 → 退规则 + 标 parse_failed，不编造

直接运行：python3 test_dataproc_resolver.py  → 退出码 0 全过，非 0 有失败。
"""
import json
import os
import sys
import tempfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from dataproc.structurer import structure, resolve
from dataproc.llms import from_config, OpenAICompatProvider


class _FakeResp:
    status_code = 200

    def __init__(self, content):
        self._c = content

    def json(self):
        return {"choices": [{"message": {"content": self._c}}]}


def main():
    fails = []
    PROD = "飞鹤 星飞帆1段 净含量800g 适合0-6个月 国食注字YP20180012"

    # RES1：规则抽取默认绿
    r1 = structure(PROD, None)
    f = r1.fields
    if f.get("stage") != "1段":
        fails.append(f"RES1: stage 应为 1段，实际 {f.get('stage')!r}")
    if "800g" not in (f.get("net_content") or ""):
        fails.append(f"RES1: net_content 应含 800g，实际 {f.get('net_content')!r}")
    if f.get("brand") != "飞鹤":
        fails.append(f"RES1: brand 应为 飞鹤，实际 {f.get('brand')!r}")
    if f.get("reg_number") != "国食注字YP20180012":
        fails.append(f"RES1: reg_number 未抽取，实际 {f.get('reg_number')!r}")
    if r1.provider_used != "rule-only":
        fails.append(f"RES1: provider_used 应为 rule-only，实际 {r1.provider_used}")
    if not fails:
        print("[PASS] RES1")

    # RES3：未知文本不编造
    r3 = structure("今天天气不错，适合散步。", None)
    if any(r3.fields.values()):
        fails.append(f"RES3: 未知文本不应抽取出字段，实际 {r3.fields}")
    else:
        print("[PASS] RES3")

    # RES2：LLM provider 接线（mock 响应，无网络）
    llm_json = json.dumps({"brand": "飞鹤", "name": "星飞帆", "stage": "1段",
                           "net_content": "800g", "age_range": "0-6个月",
                           "manufacturer": "飞鹤乳业", "reg_number": "国食注字YP20180012"},
                          ensure_ascii=False)
    prov = OpenAICompatProvider(base_url="http://fake/v1", model="test", api_key="x")
    with mock.patch("requests.post", return_value=_FakeResp(llm_json)):
        r2 = structure(PROD, prov)
    if r2.fields.get("manufacturer") != "飞鹤乳业" or r2.provider_used != "openai_compat":
        fails.append(f"RES2: LLM 接线未合并 manufacturer 或 provider 标签错 {r2.fields.get('manufacturer')!r}/{r2.provider_used}")
    else:
        print("[PASS] RES2")

    # RES6：MockProvider via from_config
    mprov = from_config(type("C", (), {"kind": "mock", "base_url": "", "model": "", "api_key": ""})())
    if mprov is None or mprov.kind != "mock":
        fails.append("RES6: from_config(kind=mock) 应返回 MockProvider")
    else:
        print("[PASS] RES6")

    # RES7：LLM JSON 解析失败 → 退规则 + parse_failed
    bad_prov = OpenAICompatProvider(base_url="http://fake/v1", model="t", api_key="x")
    with mock.patch("requests.post", return_value=_FakeResp("这不是合法JSON")):
        r7 = structure(PROD, bad_prov)
    if not r7.parse_failed or r7.fields.get("stage") != "1段":
        fails.append(f"RES7: JSON 失败应退规则(parse_failed)+保留 stage，实际 {r7.parse_failed}/{r7.fields.get('stage')}")
    else:
        print("[PASS] RES7")

    # RES4：resolve reg_number 优先
    rr = resolve({"reg_number": "国食注字YP20180012"})
    if rr["uid"] != "reg:国食注字YP20180012" or rr["status"] != "confirmed":
        fails.append(f"RES4: uid/status 错 {rr}")
    else:
        print("[PASS] RES4")

    # RES5：resolve 元组兜底
    rt = resolve({"brand": "飞鹤", "name": "星飞帆", "stage": "1段"})
    if not rt["uid"].startswith("tuple:") or rt["status"] != "pending":
        fails.append(f"RES5: 元组兜底 uid/status 错 {rt}")
    else:
        print("[PASS] RES5")

    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL GREEN (P3 结构化抽取+实体解析：RES1-RES7)")
    sys.exit(0)


if __name__ == "__main__":
    main()
