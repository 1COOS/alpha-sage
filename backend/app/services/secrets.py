from __future__ import annotations

import os

import keyring

SERVICE_NAME = "alpha-sage"


class SecretStore:
    @staticmethod
    def get_api_key() -> str | None:
        if value := os.getenv("OPENAI_API_KEY"):
            return value
        try:
            return keyring.get_password(SERVICE_NAME, "openai-api-key")
        except keyring.errors.KeyringError:
            return None

    @staticmethod
    def set_api_key(value: str) -> None:
        if not value.strip():
            raise ValueError("API key cannot be empty")
        try:
            keyring.set_password(SERVICE_NAME, "openai-api-key", value.strip())
        except keyring.errors.KeyringError as exc:
            raise RuntimeError("操作系统密钥环不可用，请改用 OPENAI_API_KEY 环境变量") from exc

    @classmethod
    def is_configured(cls) -> bool:
        return bool(cls.get_api_key())
