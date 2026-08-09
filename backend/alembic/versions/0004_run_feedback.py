"""Add persistent run progress and model failure audit fields."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("trigger_source", sa.String(length=32), nullable=False, server_default="SYSTEM"),
    )
    op.add_column(
        "agent_runs",
        sa.Column("parameters", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("agent_runs", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_runs", sa.Column("stage", sa.String(length=80), nullable=True))
    op.add_column("agent_runs", sa.Column("progress_current", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("progress_total", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("progress_message", sa.Text(), nullable=True))
    op.execute("UPDATE agent_runs SET updated_at = COALESCE(finished_at, started_at)")
    op.create_index("ix_agent_runs_status_started", "agent_runs", ["status", "started_at"], unique=False)

    op.add_column(
        "model_invocations",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="COMPLETED"),
    )
    op.add_column("model_invocations", sa.Column("latency_ms", sa.Integer(), nullable=True))
    op.add_column("model_invocations", sa.Column("http_status", sa.Integer(), nullable=True))
    op.add_column("model_invocations", sa.Column("error_type", sa.String(length=80), nullable=True))
    op.add_column("model_invocations", sa.Column("error_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_invocations", "error_message")
    op.drop_column("model_invocations", "error_type")
    op.drop_column("model_invocations", "http_status")
    op.drop_column("model_invocations", "latency_ms")
    op.drop_column("model_invocations", "status")

    op.drop_index("ix_agent_runs_status_started", table_name="agent_runs")
    op.drop_column("agent_runs", "progress_message")
    op.drop_column("agent_runs", "progress_total")
    op.drop_column("agent_runs", "progress_current")
    op.drop_column("agent_runs", "stage")
    op.drop_column("agent_runs", "updated_at")
    op.drop_column("agent_runs", "parameters")
    op.drop_column("agent_runs", "trigger_source")
