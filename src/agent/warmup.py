"""Prompt Caching 缓存预热（优化 C·阶段4，可选）。

消除新会话首条请求的 cache-miss：用一次最小请求把「稳定前缀」（消歧指令 + 已知清单）
写入 provider 缓存，后续该员工的真实请求即命中。仅当该员工预期有 ≥2 次请求时值得
（低频员工可跳过）。预热本身有一次写入成本（miss），属投资。

注意：provider 缓存以「输入前缀」为键，与 max_tokens 无关——故预热只需发送稳定前缀，
即可让后续真实请求命中，无需为输出长度特殊优化。
"""
from __future__ import annotations

import json
from typing import List

from baby.store import BabyProfileStore
from baby.resolution import _SYSTEM_INSTRUCTION, _KNOWN_HEADER


async def warmup_prompt_cache(
    store: BabyProfileStore,
    provider,
    ent: str,
    emp: str,
) -> None:
    """预热某员工的消歧 prompt 缓存（写入稳定前缀）。

    provider.complete 需接受 ``cache_control`` 关键字（CloudProvider/OllamaProvider 已支持）。
    触发时机建议：员工首次活跃 / 每日首次 / 新建宝宝后（known_json 变化需重写缓存）。
    """
    known = store.list_for_employee(ent, emp)
    known_json = json.dumps(known, ensure_ascii=False)
    system_content = _SYSTEM_INSTRUCTION + _KNOWN_HEADER + known_json
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": "预热"},
    ]
    await provider.complete(messages, cache_control=True)
