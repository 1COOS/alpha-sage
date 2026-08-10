"""Add append-only EOD research checkpoints and resume lineage."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.add_column(sa.Column("resumed_from_run_id", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_agent_runs_resumed_from",
            "agent_runs",
            ["resumed_from_run_id"],
            ["id"],
        )
        batch_op.create_index("ix_agent_runs_resumed_from", ["resumed_from_run_id"], unique=False)

    op.create_table(
        "research_phase_checkpoints",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("source_checkpoint_id", sa.String(length=64), nullable=True),
        sa.Column("instrument_id", sa.String(length=64), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("checkpoint_key", sa.String(length=120), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("model_invocation_id", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=160), nullable=False),
        sa.Column("model_version", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("strategy_version", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.ForeignKeyConstraint(["model_invocation_id"], ["model_invocations.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["source_checkpoint_id"], ["research_phase_checkpoints.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "instrument_id",
            "checkpoint_key",
            name="uq_research_checkpoint_run_phase",
        ),
    )
    op.create_index(
        "ix_research_checkpoints_run",
        "research_phase_checkpoints",
        ["run_id", "instrument_id"],
        unique=False,
    )
    op.execute(
        "CREATE TRIGGER research_phase_checkpoints_no_update "
        "BEFORE UPDATE ON research_phase_checkpoints "
        "BEGIN SELECT RAISE(ABORT, 'append-only table cannot be updated'); END"
    )
    op.execute(
        "CREATE TRIGGER research_phase_checkpoints_no_delete "
        "BEFORE DELETE ON research_phase_checkpoints "
        "BEGIN SELECT RAISE(ABORT, 'append-only table cannot be deleted'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS research_phase_checkpoints_no_delete")
    op.execute("DROP TRIGGER IF EXISTS research_phase_checkpoints_no_update")
    op.drop_index("ix_research_checkpoints_run", table_name="research_phase_checkpoints")
    op.drop_table("research_phase_checkpoints")
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_index("ix_agent_runs_resumed_from")
        batch_op.drop_constraint("fk_agent_runs_resumed_from", type_="foreignkey")
        batch_op.drop_column("resumed_from_run_id")
