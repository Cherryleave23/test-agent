"""Phase B 重新排查：端到端真实部署模拟（数据处理→部署导入→员工使用）。

每个阶段按「真人操作流程」拆解，并把观察到的不足如实记录。
使用全新的临时 DB（避免旧会话 message_id 去重污染），保证可重复。
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

HERE = "/workspace"
for p in (os.path.join(HERE, "src"), os.path.join(HERE, "tools"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
log_capture = io.StringIO()
cap_handler = logging.StreamHandler(log_capture)
cap_handler.setLevel(logging.WARNING)
logging.getLogger().addHandler(cap_handler)


async def main():
    TS = time.strftime("%Y%m%d_%H%M%S")
    ROOT = Path(tempfile.mkdtemp(prefix=f"sim_{TS}_"))
    print(f"== 模拟沙箱根目录: {ROOT}")

    from dataproc.build import build_bundle

    def write_repo(repo_dir: Path, files: dict, repo_json: dict):
        repo_dir.mkdir(parents=True, exist_ok=True)
        (repo_dir / ".dataproc").mkdir(exist_ok=True)
        (repo_dir / ".dataproc" / "repo.json").write_text(
            json.dumps(repo_json, ensure_ascii=False, indent=2), encoding="utf-8")
        for rel, content in files.items():
            fp = repo_dir / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")

    # =======================================================================
    # 阶段 1：数据处理（dataproc）
    # =======================================================================
    NORMAL_FILES = {
        "产品资料/睿护1段.md": """---
name: 睿护婴儿配方奶粉1段
brand: 贝贝优
stage: 1段
age_range: 0-6个月
price: 368
reg_number: 国食注字YP20180012
ptype: 牛奶粉
highlights: 含OPO结构脂、益生菌Bb-12、A2 β-酪蛋白
ingredients: 生牛乳、脱盐乳清粉、乳糖、植物油
nutrition: 蛋白质12.5g/100g，脂肪28g/100g，DHA 0.3%
manufacturer: 贝贝优营养品有限公司
---
# 睿护婴儿配方奶粉1段
贝贝优睿护1段适用于0-6个月新生儿，含OPO结构脂、益生菌Bb-12、A2 β-酪蛋白，新西兰奶源。
""",
        "产品资料/臻羊羊奶粉.md": """---
name: 臻羊婴儿配方羊奶粉
brand: 臻羊
stage: 1段
age_range: 0-6个月
price: 398
ptype: 羊奶粉
highlights: 100%纯羊乳蛋白、低致敏
ingredients: 羊乳清粉、脱盐羊乳清
nutrition: 蛋白质12.0g/100g，脂肪27g/100g
manufacturer: 臻羊乳业
---
# 臻羊婴儿配方羊奶粉
臻羊1段为100%纯羊乳蛋白配方，低致敏，荷兰奶源，适合牛奶蛋白过敏宝宝。
（注：本产品注册号尚未下发，内部待确认）
""",
        "知识类文章/辅食添加指南.md": """# 宝宝辅食添加指南
宝宝满6个月开始添加辅食，首选强化铁米粉。从单一食材开始，每3-5天引入一种新食物观察过敏。
辅食应细腻泥糊状，逐步过渡到碎末、小块。1岁内不加盐、不加糖、不加蜂蜜。
""",
        "原料资料/DHA藻油.md": """# DHA藻油
DHA（二十二碳六烯酸）是ω-3不饱和脂肪酸，是宝宝大脑和视网膜发育的重要营养素。
婴幼儿每日推荐摄入100mg DHA。藻油DHA植物来源、无鱼腥味、低致敏。
""",
    }
    REPO_JSON = {
        "name": "贝贝优母婴门店",
        "enterprise_id": "ent_sim",
        "namespace": "b",
        "created_at": "2026-07-23T00:00:00+08:00",
    }
    normal_repo = ROOT / "repo_normal"
    write_repo(normal_repo, NORMAL_FILES, REPO_JSON)
    normal_bundle = ROOT / "bundle_normal"
    r = build_bundle(str(normal_repo), str(normal_bundle))
    print("\n[1a] 正常仓库 build 结果:")
    print(json.dumps(r["manifest"]["counts"], ensure_ascii=False, indent=2))
    products = [json.loads(l) for l in (normal_bundle / "products.ndjson").read_text(encoding="utf-8").splitlines()]
    print("  产品状态:", [(p["fields"].get("name"), p["status"]) for p in products])

    BAD_FILES = {
        "产品资料/正常产品.md": """---
name: 正常产品A
brand: 品牌A
stage: 1段
price: 200
reg_number: 国食注字YP20990001
---
# 正常产品A
这是一个正常产品。
""",
        "产品资料wrong/被误放的文档.md": "# 错名文件夹里的文档\n这个文件放在了非标准文件夹，真人拖错位置。\n",
        "原料资料/原料标签.png": "",
    }
    bad_repo = ROOT / "repo_bad"
    write_repo(bad_repo, BAD_FILES, REPO_JSON)
    bad_bundle = ROOT / "bundle_bad"
    bad_log = io.StringIO()
    with contextlib.redirect_stderr(bad_log):
        rb = build_bundle(str(bad_repo), str(bad_bundle))
    print("\n[1b] 异常仓库（错名文件夹 + 图片无OCR）build 结果:")
    print(json.dumps(rb["manifest"]["counts"], ensure_ascii=False, indent=2))
    bad_corpus = [json.loads(l) for l in (bad_bundle / "corpus.ndjson").read_text(encoding="utf-8").splitlines()]
    print("  corpus 明细:")
    for c in bad_corpus:
        print(f"    - kind={c['kind']!r} title={c['title']!r} content_len={len(c['content'])} meta.ocr_pending={c['meta'].get('ocr_pending')}")
    blog = bad_log.getvalue().lower()
    print("  dataproc 是否有任何关于忽略/跳过/丢弃的 stderr 输出:",
          ("是" if any(k in blog for k in ("warn", "error", "忽略", "跳过", "丢弃", "dropped")) else "否（操作者完全无可见反馈）"))

    # =======================================================================
    # 阶段 2：部署 + 导入
    # =======================================================================
    inbox = ROOT / "inbox" / "ent_sim_bundle"
    shutil.copytree(normal_bundle, inbox)

    from common.config import EnterpriseConfig
    from app import build_instance

    db_path = str(ROOT / "instance.db")
    baby_db_path = str(ROOT / "baby.db")
    cfg = EnterpriseConfig(
        enterprise_id="ent_sim", enterprise_name="贝贝优母婴门店",
        llm={"kind": "mock"}, embedding={"kind": "mock"}, rerank={"kind": "none"},
        wechat={"bot_token": "test", "base_url": "https://ilinkai.weixin.qq.com"},
        db_path=db_path, baby_db_path=baby_db_path, baby_profile_enabled=True,
        system_prompt="你是母婴垂类智能顾问，服务于门店员工，基于企业产品知识库回答育儿与产品问题。",
    )
    os.environ["BUNDLE_INBOX_DIR"] = str(ROOT / "inbox")
    store, session, agent, client, gateway = build_instance(cfg)
    del os.environ["BUNDLE_INBOX_DIR"]

    con = sqlite3.connect(db_path)
    corpus_n = con.execute("SELECT COUNT(*) FROM corpus WHERE enterprise_id=?", ("ent_sim",)).fetchone()[0]
    products_n = con.execute("SELECT COUNT(*) FROM products_milk WHERE enterprise_id=?", ("ent_sim",)).fetchone()[0]
    pending = store.list_pending_products("ent_sim")
    print("\n[2] 部署启动自动导入结果:")
    print(f"  corpus 条数(ent_sim)={corpus_n}  产品条数(ent_sim)={products_n}  待确认商品数={len(pending)}")
    for p in pending:
        print(f"    pending: {p.get('name')} [{p['table']}] 缺失字段={p['pending_key']}")

    try:
        coll = store._col
        allm = coll.get(where={"enterprise_id": "ent_sim"}, limit=100)
        kinds = {}
        for m in allm["metadatas"]:
            k = m.get("kind", "<空>")
            kinds[k] = kinds.get(k, 0) + 1
        print(f"  Chroma kind 分布(ent_sim): {kinds}")
    except Exception as e:
        print(f"  Chroma kind 分布读取失败: {e}")

    def top_kind(q):
        hits = store.retrieve(q, "ent_sim", top_k=3)
        return (hits[0].meta.get("kind") if hits else None, hits[0].title if hits else None)
    print("  检索路由抽样:")
    for q in ["睿护1段有什么特点", "宝宝辅食怎么添加", "DHA有什么作用"]:
        k, t = top_kind(q)
        print(f"    Q={q!r} -> top_kind={k}  title={t!r}")

    # =======================================================================
    # 阶段 3：员工使用
    # =======================================================================
    sent_log = []
    async def _noop_send(emp, text, ctx):
        sent_log.append((emp, text))
        return {"ok": True}
    client.send_message = _noop_send

    from wechat.ilink_client import IncomingMessage

    EMP_A = "employee_zhang"
    EMP_B = "employee_li"
    q_product = "睿护1段奶粉有什么特点？"
    q_article = "宝宝6个月了应该怎么添加辅食？"
    q_ingredient = "DHA对宝宝有什么好处？"
    q_pending = "臻羊羊奶粉多少钱、适合什么宝宝？"
    q_a_unique = "【员工A专有】今天门店A的会员日活动还有吗？"   # 仅 A 问
    q_b_unique = "【员工B专有】门店B的退换货政策是什么？"       # 仅 B 问

    async def chat(emp, qs):
        out = []
        for i, q in enumerate(qs):
            msg = IncomingMessage(message_id=f"{emp}-{i}", from_user_id=emp, content=q)
            try:
                ans = await gateway.handle_message(msg, None)
            except Exception as e:
                out.append((q, "EXCEPTION", f"{type(e).__name__}: {e}"))
                continue
            if ans is None:
                out.append((q, "NONE(去重)", None))
            else:
                out.append((q, "OK", ans.text))
        return out

    print("\n[3] 员工使用模拟（员工A 与 员工B 并发会话）")
    res_a = await chat(EMP_A, [q_product, q_article, q_ingredient, q_pending, q_a_unique])
    res_b = await chat(EMP_B, [q_product, q_article, q_b_unique])

    for emp, res in ((EMP_A, res_a), (EMP_B, res_b)):
        print(f"\n  --- 员工 {emp} ---")
        for q, status, text in res:
            snip = (text[:70] + "…") if text and len(text) > 70 else text
            print(f"    [{status}] {q}\n        -> {snip}")

    sid_a = session.session_key("ent_sim", EMP_A, EMP_A)
    sid_b = session.session_key("ent_sim", EMP_B, EMP_B)
    print(f"\n[3-隔离] session key: A={sid_a}  B={sid_b}  不同={sid_a != sid_b}")
    hist_a = [t.content for t in session.history(session.get_or_create('ent_sim', EMP_A, EMP_A))]
    hist_b = [t.content for t in session.history(session.get_or_create('ent_sim', EMP_B, EMP_B))]
    # 用「对方专有提问」是否泄漏进本方历史来判定串档（避免两人同问导致的误报）
    leak = (q_b_unique in hist_a) or (q_a_unique in hist_b)
    print(f"  A历史条数={len(hist_a)}  B历史条数={len(hist_b)}  "
          f"对方专有提问串入本方={leak}（应为 False）")

    print("\n[3-待确认缺口] 员工问待注册商品，agent 实际回复是否含该商品：")
    pending_names = [p.get("name") for p in pending]
    ans_pending = res_a[3][2] or ""
    surfaced = [n for n in pending_names if n and n in ans_pending]
    print(f"    待确认商品={pending_names}  在回复中被推荐={surfaced}")
    print(f"    回复片段: {ans_pending[:160]}")

    before = len(sent_log)
    dup = await gateway.handle_message(
        IncomingMessage(message_id=f"{EMP_A}-0", from_user_id=EMP_A, content=q_product), None)
    after = len(sent_log)
    print(f"\n[3-去重] 重发同 message_id: handle_message 返回={dup!r}  新增发送={after - before}（期望 0）")

    q_baby = "客户李姐家宝宝8个月，牛奶蛋白过敏，推荐什么？"
    baby_msg = IncomingMessage(message_id=f"{EMP_A}-5", from_user_id=EMP_A, content=q_baby)
    try:
        ans_baby = await gateway.handle_message(baby_msg, None)
        baby_text = ans_baby.text if ans_baby else None
    except Exception as e:
        baby_text = f"EXCEPTION {type(e).__name__}: {e}"
    print(f"\n[3-宝宝建档] 带客户信号消息: 回复片段={ (baby_text[:90] if baby_text else baby_text) }")
    try:
        babies = gateway.baby_store.list_for_employee("ent_sim", EMP_A)
        print(f"    员工A 已建档宝宝数={len(babies)}（mock 下应≈0，因 LLM 不解析）")
    except Exception as e:
        print(f"    查询建档异常: {e}")

    warn_text = log_capture.getvalue()
    gw_warns = [l for l in warn_text.splitlines() if ("wechat.gateway" in l or "降级" in l or "消歧" in l)]
    print(f"\n[3-网关日志] WARNING/降级类: {gw_warns if gw_warns else '（无）'}")
    print(f"  全部捕获的 WARNING 日志行数: {len(warn_text.splitlines())}")

    print("\n== 模拟结束 ==")


if __name__ == "__main__":
    asyncio.run(main())
