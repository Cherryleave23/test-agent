#!/usr/bin/env python3
# @module e2e
"""端到端最小闭环 harness（controlled-vibe-coding：真实运行判 PASS/FAIL）。

场景：mock iLink 服务器 + Mock LLM，跑通
  「微信消息 → 会话隔离 → RAG 问答 → 回复发微信」。

断言：
  H1 检索+RAG：问奶粉，回答引用了入库的奶粉产品（带引用/品牌）。
  H2 多员工隔离：员工 A、B 各自提问，回复只发给各自，且历史不串扰。
  H3 去重：同 message_id 重发不重复处理。
  H4 防幻觉：知识库无相关内容时，明确告知暂无信息。
  H5 企业隔离：检索不跨企业（A 企业查不到 B 企业产品）。

直接运行：python3 test_e2e_closed_loop.py  → 退出码 0 全过，非 0 有失败。
"""
import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common.config import EnterpriseConfig, LLMConfig, EmbeddingConfig, WechatConfig  # noqa: E402
from app import build_instance, seed_demo  # noqa: E402
from wechat.mock_server import MockILinkServer, make_wechat_config  # noqa: E402


def _cfg(ent_id: str, port: int) -> EnterpriseConfig:
    return EnterpriseConfig(
        enterprise_id=ent_id,
        enterprise_name=ent_id,
        llm=LLMConfig(kind="mock"),
        embedding=EmbeddingConfig(kind="mock"),
        wechat=make_wechat_config(port),
        db_path=os.path.join(tempfile.mkdtemp(), "inst.db"),
    )


async def _start(ent_id: str, port: int, messages: list):
    cfg = _cfg(ent_id, port)
    seed_demo(cfg.enterprise_id, cfg.db_path)
    store, session, agent, client, gateway = build_instance(cfg)
    server = MockILinkServer(token="t")
    actual_port = server.start(port=port)
    client.cfg.base_url = f"http://127.0.0.1:{actual_port}"
    for emp, content, mid in messages:
        server.inject_message(emp, content, mid)
    for _ in range(6):
        await gateway.run_once()
        await asyncio.sleep(0.05)
    return gateway, server


async def _h1_retrieval_and_rag():
    gw, server = await _start("ent_a", 0, [
        ("emp_li", "你们有适合0-6个月宝宝的1段奶粉吗？", "m1"),
    ])
    sent = server.sent_to("emp_li")
    assert sent, "应当有回复发出"
    text = sent[-1]["content"]
    assert "睿护" in text and "1段" in text, f"应引用入库奶粉，实际: {text}"
    assert "不构成医疗诊断" in text, "应附母婴免责"


async def _h2_multi_employee_isolation():
    gw, server = await _start("ent_a", 0, [
        ("emp_li", "推荐一款1段牛奶粉", "m1"),
        ("emp_wang", "推荐一款DHA营养品", "m2"),
    ])
    li = server.sent_to("emp_li")
    wang = server.sent_to("emp_wang")
    assert li and wang, "两位员工都应收到回复"
    assert li[-1]["content"] != wang[-1]["content"]
    assert "睿护" in li[-1]["content"]
    assert "DHA" in wang[-1]["content"]
    sid_li = gw.session.get_or_create("ent_a", "emp_li", "emp_li")
    hist = gw.session.history(sid_li)
    assert all("DHA营养品" not in t.content for t in hist), "A 员工历史不应含 B 的问题"


async def _h3_dedup():
    gw, server = await _start("ent_a", 0, [
        ("emp_li", "推荐一款1段牛奶粉", "dup1"),
        ("emp_li", "推荐一款1段牛奶粉", "dup1"),
    ])
    li = server.sent_to("emp_li")
    assert len(li) == 1, f"同 message_id 应只回复一次，实际 {len(li)} 条"


async def _h4_no_hallucination():
    gw, server = await _start("ent_a", 0, [
        ("emp_li", "你们卖汽车轮胎吗？", "m_x"),
    ])
    sent = server.sent_to("emp_li")
    assert sent, "仍应有回复"
    assert "暂无相关" in sent[-1]["content"], f"无命中应告知，实际: {sent[-1]['content']}"


async def _h5_enterprise_isolation():
    cfg_a = _cfg("ent_a", 0)
    seed_demo("ent_a", cfg_a.db_path)
    _, _, agent_a, _, _ = build_instance(cfg_a)
    cfg_b = _cfg("ent_b", 0)
    store_b, session_b, agent_b, _, _ = build_instance(cfg_b)
    ans_a = await agent_a.answer("1段牛奶粉")
    ans_b = await agent_b.answer("1段牛奶粉")
    assert ans_a.hits, "A 企业应检索到产品"
    assert ans_b.empty, "B 企业（无数据）不应检索到 A 的产品"


CHECKS = [
    ("H1 检索+RAG", _h1_retrieval_and_rag),
    ("H2 多员工隔离", _h2_multi_employee_isolation),
    ("H3 去重", _h3_dedup),
    ("H4 防幻觉", _h4_no_hallucination),
    ("H5 企业隔离", _h5_enterprise_isolation),
]


async def main():
    failed = []
    for name, fn in CHECKS:
        try:
            await fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {name}: 异常 {type(e).__name__}: {e}")
            failed.append(name)
    print(f"=== Summary: {len(CHECKS) - len(failed)}/{len(CHECKS)} passed ===")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("RESULT: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
