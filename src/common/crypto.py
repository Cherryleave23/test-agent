"""数据静态加密（MOD-deploy P0-3：健康数据加密）。

宝宝健康档案含敏感健康信息（月龄/过敏史/出生日期/病史等），端侧落库须加密。
采用纯 Python `cryptography` 的 Fernet（AES-128-CBC + HMAC-SHA256，对称），
不引入 SQLCipher，保证 embeddable Python 可移植、harness 可单测。

设计要点：
- 密钥来自环境变量 ``AGENT_DATA_ENCRYPTION_KEY``（base64 编码的 32 字节 Fernet key）。
- 加密值在落库前加 ``fernet:`` 前缀；读取时若带前缀则解密，否则视为明文
  （兼容升级前的明文行 —— 惰性迁移）。
- 开发/mock 模式未设密钥时用**确定性 dev key** 并 ``logger.warning``，
  但**生产/部署模式**（``require=True``）缺密钥直接抛 ``KeyMissing``，
  禁止静默明文落库。
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_PREFIX = "fernet:"
# 确定性 dev key（仅开发/mock，禁止用于真实生产数据）：32 字节固定值。
_DEV_KEY_B64 = "ZGV2LW9ubHktaW5zZWN1cmUtYnV0LWZpeGVkLTMyYmI="


class KeyMissing(RuntimeError):
    """生产/部署模式要求加密密钥但未提供。"""


class Vault:
    """Fernet 对称加密封装。单例通过 :func:`get_vault` 获取。"""

    def __init__(self, key: str, dev_mode: bool = False):
        # Fernet 接受 base64 字符串作为密钥（非解码后的原始字节）
        self._f = Fernet(key)
        self.dev_mode = dev_mode

    @classmethod
    def from_env(cls, *, require: bool = False) -> "Vault":
        """从环境构建 Vault。

        ``require=True`` 时（生产/部署模式）缺 ``AGENT_DATA_ENCRYPTION_KEY`` 抛
        :class:`KeyMissing`；否则回退确定性 dev key 并发警告。
        """
        raw = (os.environ.get("AGENT_DATA_ENCRYPTION_KEY") or "").strip()
        if raw:
            try:
                decoded = base64.urlsafe_b64decode(raw)
            except Exception as e:  # noqa: BLE001
                raise KeyMissing(f"AGENT_DATA_ENCRYPTION_KEY 不是合法 base64：{e}")
            if len(decoded) != 32:
                raise KeyMissing("AGENT_DATA_ENCRYPTION_KEY 必须是 32 字节 base64 编码")
            return cls(raw, dev_mode=False)
        if require:
            raise KeyMissing(
                "AGENT_DATA_ENCRYPTION_KEY 未设置（生产/部署模式要求健康数据加密密钥，"
                "禁止明文落库）"
            )
        logger.warning(
            "AGENT_DATA_ENCRYPTION_KEY 未设置，使用确定性 dev key 加密（仅限开发/mock，"
            "禁止用于生产环境真实健康数据）"
        )
        return cls(_DEV_KEY_B64, dev_mode=True)

    def encrypt(self, plaintext: str) -> str:
        """加密字符串，返回带 ``fernet:`` 前缀的 token。"""
        token = self._f.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return _PREFIX + token

    def decrypt(self, value: str) -> str:
        """解密；无前缀视为明文（惰性迁移兼容）。非字符串原样返回。"""
        if not isinstance(value, str):
            return value  # 旧库数字（budget/gestational_weeks）保持原样
        if not value.startswith(_PREFIX):
            return value
        try:
            return self._f.decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as e:
            # 前缀存在但解密失败 = 密钥不匹配/数据损坏，必须显式报错而非静默。
            raise InvalidToken(f"健康数据解密失败（密钥不匹配或数据损坏）：{e}")


_VAULT: Optional[Vault] = None


def get_vault(*, require: bool = False) -> Vault:
    """获取（缓存的）单例 Vault。``require=True`` 用于生产启动校验。"""
    global _VAULT
    if _VAULT is None:
        _VAULT = Vault.from_env(require=require)
    return _VAULT


def reset_vault() -> None:
    """测试用：清空缓存，使下一次 ``get_vault`` 重新读环境。"""
    global _VAULT
    _VAULT = None
