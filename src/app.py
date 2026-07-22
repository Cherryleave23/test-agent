"""顶层装配：把一个企业实例的模块连起来（1 企业 1 实例，G6）。

build_instance(cfg) -> (KnowledgeStore, SessionStore, Agent, ILinkClient, WechatGateway)
"""
from __future__ import annotations

from common.config import EnterpriseConfig
from kb.store import KnowledgeStore
from session.store import SessionStore
from agent.pipeline import Agent
from wechat.ilink_client import ILinkClient
from wechat.gateway import WechatGateway
from baby.store import BabyProfileStore
from ingest.protocol import SeedAdapter
from ingest.importer import load_on_startup  # F4：启动扫收件箱自动加载 bundle
from kb.models import MilkProduct, NutritionProduct


def build_instance(cfg: EnterpriseConfig):
    store = KnowledgeStore(cfg.db_path, embedding_kind=cfg.embedding.kind,
                           rerank_kind=cfg.rerank.kind)
    # F4：agent 启动钩子——若配置了 BUNDLE_INBOX_DIR，自动扫目录加载 dataproc 产出的 bundle；
    # 未配置（默认/测试）则为 no-op，不影响既有行为。
    load_on_startup(store, cfg.enterprise_id)
    session = SessionStore(cfg.db_path)
    # A1 修复：装配 BabyProfileStore 并传给 WechatGateway
    # 原缺陷：build_instance 未创建 baby_store，导致 gateway.baby_store 永远 None，
    #         整个 MOD-baby-profile 在端侧运行时完全失效（无建档/归档/消歧/注入）。
    baby_store = BabyProfileStore(cfg.baby_db_path or cfg.db_path)
    agent = Agent(cfg, store)
    client = ILinkClient(cfg.wechat)
    gateway = WechatGateway(cfg, session, agent, client, baby_store=baby_store)
    return store, session, agent, client, gateway


def seed_demo(ent: str, db_path: str) -> None:
    """灌入演示数据：HQ 知识 + 一家企业的奶粉/营养品。"""
    store = KnowledgeStore(db_path, embedding_kind="mock")
    seed = SeedAdapter(store, ent)
    seed.seed_hq_knowledge()
    seed.seed_products(
        milks=[
            MilkProduct(
                enterprise_id=ent, name="睿护婴儿配方奶粉1段", brand="贝贝优",
                stage="1段", age_range="0-6个月", price=368.0, origin="中国",
                milk_origin="新西兰", ptype="牛奶粉", reg_number="国食注字YP20180012",
                manufacturer="贝贝优营养品有限公司",
                ingredients="生牛乳、脱盐乳清粉、乳糖、植物油（棕榈油、大豆油）…",
                nutrition="蛋白质12.5g/100g，脂肪28g/100g，DHA 0.3%…",
                highlights="含OPO结构脂、益生菌Bb-12、A2 β-酪蛋白",
            ),
            MilkProduct(
                enterprise_id=ent, name="羊羊乐婴儿配方羊奶粉2段", brand="羊羊乐",
                stage="2段", age_range="6-12个月", price=428.0, origin="中国",
                milk_origin="荷兰", ptype="羊奶粉", reg_number="国食注字YP20190088",
                manufacturer="羊羊乐乳业",
                ingredients="羊乳清粉、脱盐羊乳清、植物油…",
                nutrition="蛋白质12.0g/100g，脂肪27g/100g…",
                highlights="100%纯羊乳蛋白、低致敏、益生元组合",
            ),
        ],
        nutritions=[
            NutritionProduct(
                enterprise_id=ent, name="小鱼仔DHA藻油", brand="智宝宝",
                category="DHA", audience="婴幼儿", dosage_form="滴剂",
                age_range="0-3岁", price=158.0, origin="中国",
                manufacturer="智宝宝生物", health_license="国食健字G20170123",
                efficacy="补脑护眼、助力大脑发育",
                ingredients="DHA藻油、葵花籽油",
                nutrition="每粒含DHA 100mg",
                highlights="植物性藻油、无鱼腥味",
                cautions="婴幼儿需在成人监护下食用",
            ),
        ],
    )
