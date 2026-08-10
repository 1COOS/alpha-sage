from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder
from sqlalchemy import DateTime
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

SHANGHAI = ZoneInfo("Asia/Shanghai")


def beijing_now() -> datetime:
    return datetime.now(SHANGHAI)


def beijing_today() -> date:
    return beijing_now().date()


def beijing_day_start_utc(value: date | None = None) -> datetime:
    local_day = value or beijing_today()
    return datetime.combine(local_day, time.min, SHANGHAI).astimezone(UTC)


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime 必须包含明确时区偏移")
    return value


def to_utc(value: datetime) -> datetime:
    return require_aware(value).astimezone(UTC)


def restore_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_beijing(value: datetime) -> datetime:
    return require_aware(value).astimezone(SHANGHAI)


def beijing_isoformat(value: datetime) -> str:
    return to_beijing(value).isoformat()


def api_jsonable(value: Any) -> Any:
    return jsonable_encoder(value, custom_encoder={datetime: beijing_isoformat})


class UTCDateTime(TypeDecorator[datetime]):
    """Persist UTC instants while restoring timezone awareness on SQLite reads."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        normalized = to_utc(value)
        if dialect.name == "sqlite":
            return normalized.replace(tzinfo=None)
        return normalized

    def process_result_value(self, value: datetime | None, _dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return restore_utc(value)
