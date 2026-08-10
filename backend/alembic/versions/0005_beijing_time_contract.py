"""Append canonical UTC corrections for proven legacy local timestamps."""

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHANGHAI = ZoneInfo("Asia/Shanghai")
LOCAL_ARTIFACT_PROVIDERS = (
    "baostock",
    "eastmoney",
    "eastmoney+baostock",
    "tencent-history+baostock",
)


def _canonical_utc_wall_time(raw: datetime | str) -> datetime:
    value = raw if isinstance(raw, datetime) else datetime.fromisoformat(raw)
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=SHANGHAI)
    return value.astimezone(UTC).replace(tzinfo=None)


def _original_value(raw: datetime | str) -> str:
    return raw.isoformat(sep=" ") if isinstance(raw, datetime) else raw


def _correction_id(entity_type: str, entity_id: str, field_name: str) -> str:
    digest = hashlib.sha256(f"{entity_type}:{entity_id}:{field_name}".encode()).hexdigest()[:32]
    return f"tc_{digest}"


def _assert_unambiguous_history(connection: sa.Connection) -> None:
    ambiguous_evidence = int(connection.scalar(sa.text("SELECT COUNT(*) FROM evidence_refs")) or 0)
    if ambiguous_evidence:
        raise RuntimeError(
            "检测到旧 evidence_refs.published_at；原始时区无法从 SQLite 证明，请先完成人工来源核对，迁移不会猜测转换"
        )


def _create_correction_store(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            "CREATE TABLE IF NOT EXISTS time_corrections ("
            "id VARCHAR(64) NOT NULL PRIMARY KEY, "
            "entity_type VARCHAR(80) NOT NULL, "
            "entity_id VARCHAR(64) NOT NULL, "
            "field_name VARCHAR(80) NOT NULL, "
            "original_value TEXT NOT NULL, "
            "canonical_utc DATETIME NOT NULL, "
            "reason TEXT NOT NULL, "
            "created_at DATETIME NOT NULL, "
            "CONSTRAINT uq_time_correction_target UNIQUE (entity_type, entity_id, field_name)"
            ")"
        )
    )
    connection.execute(
        sa.text("CREATE INDEX IF NOT EXISTS ix_time_corrections_target ON time_corrections (entity_type, entity_id)")
    )


def _append_corrections(
    connection: sa.Connection,
    *,
    select_sql: str,
    entity_type: str,
    field_name: str,
    value_column: str,
    reason: str,
    parameters: dict[str, object] | None = None,
) -> int:
    rows = list(connection.execute(sa.text(select_sql), parameters or {}).mappings())
    created_at = datetime.now(UTC).replace(tzinfo=None)
    for row in rows:
        connection.execute(
            sa.text(
                "INSERT OR IGNORE INTO time_corrections "
                "(id, entity_type, entity_id, field_name, original_value, canonical_utc, reason, created_at) "
                "VALUES (:id, :entity_type, :entity_id, :field_name, :original_value, "
                ":canonical_utc, :reason, :created_at)"
            ),
            {
                "id": _correction_id(entity_type, row["id"], field_name),
                "entity_type": entity_type,
                "entity_id": row["id"],
                "field_name": field_name,
                "original_value": _original_value(row[value_column]),
                "canonical_utc": _canonical_utc_wall_time(row[value_column]),
                "reason": reason,
                "created_at": created_at,
            },
        )
    return len(rows)


def _append_historical_time_corrections(connection: sa.Connection) -> dict[str, int]:
    provider_params = {f"provider_{index}": provider for index, provider in enumerate(LOCAL_ARTIFACT_PROVIDERS)}
    provider_placeholders = ", ".join(f":provider_{index}" for index in range(len(LOCAL_ARTIFACT_PROVIDERS)))
    artifacts = _append_corrections(
        connection,
        select_sql=(f"SELECT id, available_at FROM data_artifacts WHERE provider IN ({provider_placeholders})"),
        entity_type="DataArtifact",
        field_name="available_at",
        value_column="available_at",
        reason="legacy provider fetch time was persisted as Asia/Shanghai wall time",
        parameters=provider_params,
    )
    cash_ledger = _append_corrections(
        connection,
        select_sql=("SELECT id, occurred_at FROM cash_ledger WHERE event_type IN ('PAPER_BUY', 'PAPER_SELL')"),
        entity_type="CashLedgerEntry",
        field_name="occurred_at",
        value_column="occurred_at",
        reason="legacy paper trade occurrence used the exchange-local bar timestamp",
    )
    closed_lots = _append_corrections(
        connection,
        select_sql="SELECT id, closed_at FROM position_lots WHERE closed_at IS NOT NULL",
        entity_type="PositionLot",
        field_name="closed_at",
        value_column="closed_at",
        reason="legacy lot close time used the host-local Asia/Shanghai timestamp",
    )
    return {
        "DataArtifact.available_at": artifacts,
        "CashLedgerEntry.occurred_at": cash_ledger,
        "PositionLot.closed_at": closed_lots,
    }


def upgrade() -> None:
    connection = op.get_bind()
    _assert_unambiguous_history(connection)
    _create_correction_store(connection)
    _append_historical_time_corrections(connection)
    connection.execute(
        sa.text(
            "CREATE TRIGGER IF NOT EXISTS time_corrections_no_update "
            "BEFORE UPDATE ON time_corrections "
            "BEGIN SELECT RAISE(ABORT, 'append-only table cannot be updated'); END"
        )
    )
    connection.execute(
        sa.text(
            "CREATE TRIGGER IF NOT EXISTS time_corrections_no_delete "
            "BEFORE DELETE ON time_corrections "
            "BEGIN SELECT RAISE(ABORT, 'append-only table cannot be deleted'); END"
        )
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS time_corrections_no_delete")
    op.execute("DROP TRIGGER IF EXISTS time_corrections_no_update")
    op.drop_index("ix_time_corrections_target", table_name="time_corrections")
    op.drop_table("time_corrections")
