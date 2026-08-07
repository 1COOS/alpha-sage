from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 7777
    web_origin: str = "http://localhost:8888"

    database_url: str = f"sqlite:///{PROJECT_ROOT / 'data' / 'alpha_sage.db'}"
    artifact_root: Path = PROJECT_ROOT / "data" / "artifacts"
    raw_data_root: Path = PROJECT_ROOT / "data" / "raw"

    openai_api_key: str | None = Field(default=None, repr=False)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_mode: str = "responses"
    reasoning_model: str = "gpt-5.2"
    fast_model: str = "gpt-5-mini"

    scheduler_enabled: bool = True
    live_source_smoke_enabled: bool = False
    eastmoney_base_url: str = "https://push2his.eastmoney.com"
    eastmoney_list_url: str = "https://80.push2.eastmoney.com"
    tencent_quote_url: str = "https://qt.gtimg.cn"
    tencent_history_url: str = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

    request_timeout_seconds: float = 20.0
    intraday_call_cap: int = 12
    intraday_symbol_call_cap: int = 2
    intraday_cooldown_minutes: int = 15

    @field_validator("database_url", mode="after")
    @classmethod
    def resolve_sqlite_url(cls, value: str) -> str:
        for prefix in ("sqlite:///", "sqlite+pysqlite:///"):
            if value.startswith(prefix):
                raw_path = value.removeprefix(prefix)
                if raw_path == ":memory:" or Path(raw_path).is_absolute():
                    return value
                return f"{prefix}{(PROJECT_ROOT / raw_path).resolve()}"
        return value

    @field_validator("artifact_root", "raw_data_root", mode="after")
    @classmethod
    def resolve_storage_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    def ensure_directories(self) -> None:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.raw_data_root.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
