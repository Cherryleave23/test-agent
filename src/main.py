"""端侧实例入口（MOD-deploy，G6）。

1 家企业 1 个进程：加载配置 → 装配模块 → 扫码登录 iLink → 长轮询运行网关。
"""
from __future__ import annotations

import asyncio
import logging
import sys

from common.config import EnterpriseConfig
from app import build_instance, seed_demo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main(config_path: str):
    cfg = EnterpriseConfig.from_yaml_with_env(config_path)
    logging.info("启动企业实例: %s (%s)", cfg.enterprise_id, cfg.enterprise_name)
    logging.info("LLM 模式: %s | Embedding 模式: %s | DB: %s",
                 cfg.llm.kind, cfg.embedding.kind, cfg.db_path)
    store, session, agent, client, gateway = build_instance(cfg)
    # 首次运行灌入演示数据（可改为从 HQ 商品库 onboarding）
    if cfg.llm.kind == "mock":
        seed_demo(cfg.enterprise_id, cfg.db_path)
    # 扫码登录（生产走 qr_login；此处仅打印状态）
    qr = await client.get_qr_code()
    logging.info("iLink 登录二维码: %s", qr)
    logging.info("网关开始长轮询...")
    await gateway.run_forever()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "deploy/enterprise.yaml"
    asyncio.run(main(path))
