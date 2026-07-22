"""诊断脚本：输出 seed=42 下的信息打乱顺序与问题分配（用于报告）。"""
import os, sys, random, asyncio
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "harness"))
from baby.models import BabyProfile
from baby.store import BabyProfileStore
from baby.archive import resolve_and_archive
from test_ultimate_baby_harness import (
    BABY_GROUND_TRUTHS, SHATTERED_INFO, QUESTION_TEMPLATES, SEED,
    DisambigMockProvider, MockKBStore,
)
from agent.pipeline import Agent
from common.config import EnterpriseConfig, LLMConfig

async def main():
    rng = random.Random(SEED)
    shuffled = list(SHATTERED_INFO)
    rng.shuffle(shuffled)
    print("=== 碎片信息发送顺序（seed=42）===")
    for i, (msg, key) in enumerate(shuffled, 1):
        name = BABY_GROUND_TRUTHS[key]["name"]
        print(f"{i:2d}. [{key}/{name}] {msg}")

    baby_keys = list(BABY_GROUND_TRUTHS.keys())
    rng.shuffle(baby_keys)
    questions = []
    for i, template in enumerate(QUESTION_TEMPLATES):
        key = baby_keys[i]
        gt = BABY_GROUND_TRUTHS[key]
        questions.append((template.format(name=gt["name"]), key))
    rng.shuffle(questions)
    print("\n=== 问题随机分配顺序（seed=42）===")
    for i, (q, key) in enumerate(questions, 1):
        print(f"Q{i}. [{key}/{BABY_GROUND_TRUTHS[key]['name']}] {q}")

asyncio.run(main())
