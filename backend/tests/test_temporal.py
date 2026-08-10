from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import StatementError

from app.api.routes import router
from app.db import get_session
from app.models import AgentRun, SourceHealth
from app.temporal import api_jsonable


def test_utc_datetime_normalizes_aware_values_and_restores_timezone(session):
    row = session.get(SourceHealth, "baostock")
    assert row is not None
    row.last_checked_at = datetime(2026, 8, 10, 18, 22, 57, 123456, tzinfo=ZoneInfo("Asia/Shanghai"))
    session.commit()
    session.expire_all()

    restored = session.get(SourceHealth, "baostock")
    assert restored is not None
    assert restored.last_checked_at == datetime(2026, 8, 10, 10, 22, 57, 123456, tzinfo=UTC)
    raw = session.execute(text("SELECT last_checked_at FROM source_health WHERE source_id = 'baostock'")).scalar_one()
    assert raw == "2026-08-10 10:22:57.123456"


def test_utc_datetime_rejects_naive_values(session):
    row = session.get(SourceHealth, "baostock")
    assert row is not None
    row.last_checked_at = datetime(2026, 8, 10, 10, 22, 57)
    with pytest.raises(StatementError, match="datetime 必须包含明确时区偏移"):
        session.commit()
    session.rollback()


def test_api_jsonable_recursively_emits_beijing_offset():
    encoded = api_jsonable({"nested": [{"time": datetime(2026, 8, 10, 2, 22, 57, tzinfo=UTC)}]})
    assert encoded["nested"][0]["time"] == "2026-08-10T10:22:57+08:00"


async def test_api_emits_beijing_time_and_rejects_naive_evidence(session):
    run = AgentRun(kind="EOD", status="COMPLETED", trigger_source="MANUAL")
    session.add(run)
    session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_session():
        yield session

    test_app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/api/v1/health")
        runs = await client.get("/api/v1/agent/runs", params={"limit": 1})
        naive = await client.post(
            "/api/v1/evidence",
            json={
                "source_id": "sse",
                "source_uri": "https://www.sse.com.cn/notice",
                "title": "测试公告",
                "excerpt": "用于验证时区输入契约",
                "published_at": "2026-08-10T10:22:57",
                "credibility": "OFFICIAL",
            },
        )
        aware = await client.post(
            "/api/v1/evidence",
            json={
                "source_id": "sse",
                "source_uri": "https://www.sse.com.cn/notice",
                "title": "测试公告",
                "excerpt": "用于验证时区输入契约",
                "published_at": "2026-08-10T02:22:57Z",
                "credibility": "OFFICIAL",
            },
        )

    assert health.status_code == 200
    assert health.json()["time"].endswith("+08:00")
    assert runs.status_code == 200
    assert runs.json()[0]["started_at"].endswith("+08:00")
    assert naive.status_code == 422
    assert aware.status_code == 200
    assert aware.json()["published_at"] == "2026-08-10T10:22:57+08:00"


def _load_migration_module():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0005_beijing_time_contract.py"
    spec = importlib.util.spec_from_file_location("migration_0005", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_only_normalizes_proven_legacy_local_times():
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE evidence_refs (id TEXT, published_at DATETIME)"))
        connection.execute(sa.text("CREATE TABLE data_artifacts (id TEXT, provider TEXT, available_at DATETIME)"))
        connection.execute(sa.text("CREATE TABLE cash_ledger (id TEXT, event_type TEXT, occurred_at DATETIME)"))
        connection.execute(sa.text("CREATE TABLE position_lots (id TEXT, closed_at DATETIME)"))
        connection.execute(
            sa.text(
                "INSERT INTO data_artifacts VALUES "
                "('bao', 'baostock', '2026-08-07 13:49:08.026421'), "
                "('tencent', 'tencent-history', '2026-08-07 05:49:08.026421')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO cash_ledger VALUES "
                "('trade', 'PAPER_BUY', '2026-08-07 13:49:08.026421'), "
                "('capital', 'INITIAL_CAPITAL', '2026-08-07 05:49:08.026421')"
            )
        )
        connection.execute(sa.text("INSERT INTO position_lots VALUES ('lot', '2026-08-07 13:49:08.026421')"))

        migration = _load_migration_module()
        migration._assert_unambiguous_history(connection)
        migration._create_correction_store(connection)
        counts = migration._append_historical_time_corrections(connection)

        artifacts = {
            row.id: row.available_at
            for row in connection.execute(sa.text("SELECT id, available_at FROM data_artifacts"))
        }
        ledger = {
            row.id: row.occurred_at for row in connection.execute(sa.text("SELECT id, occurred_at FROM cash_ledger"))
        }
        closed = connection.scalar(sa.text("SELECT closed_at FROM position_lots WHERE id = 'lot'"))
        corrections = {
            (row.entity_type, row.entity_id, row.field_name): (row.original_value, row.canonical_utc)
            for row in connection.execute(
                sa.text(
                    "SELECT entity_type, entity_id, field_name, original_value, canonical_utc FROM time_corrections"
                )
            )
        }

    assert counts == {
        "DataArtifact.available_at": 1,
        "CashLedgerEntry.occurred_at": 1,
        "PositionLot.closed_at": 1,
    }
    assert artifacts["bao"] == "2026-08-07 13:49:08.026421"
    assert artifacts["tencent"] == "2026-08-07 05:49:08.026421"
    assert ledger["trade"] == "2026-08-07 13:49:08.026421"
    assert ledger["capital"] == "2026-08-07 05:49:08.026421"
    assert closed == "2026-08-07 13:49:08.026421"
    assert corrections[("DataArtifact", "bao", "available_at")] == (
        "2026-08-07 13:49:08.026421",
        "2026-08-07 05:49:08.026421",
    )
    assert corrections[("CashLedgerEntry", "trade", "occurred_at")][1] == "2026-08-07 05:49:08.026421"
    assert corrections[("PositionLot", "lot", "closed_at")][1] == "2026-08-07 05:49:08.026421"


def test_migration_blocks_ambiguous_historical_evidence():
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE evidence_refs (id TEXT, published_at DATETIME)"))
        connection.execute(sa.text("CREATE TABLE data_artifacts (id TEXT, provider TEXT, available_at DATETIME)"))
        connection.execute(sa.text("CREATE TABLE cash_ledger (id TEXT, event_type TEXT, occurred_at DATETIME)"))
        connection.execute(sa.text("CREATE TABLE position_lots (id TEXT, closed_at DATETIME)"))
        connection.execute(sa.text("INSERT INTO evidence_refs VALUES ('evidence', '2026-08-07 13:49:08.026421')"))
        migration = _load_migration_module()
        with pytest.raises(RuntimeError, match="原始时区无法从 SQLite 证明"):
            migration._assert_unambiguous_history(connection)
