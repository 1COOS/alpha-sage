"""market execution trace

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-06 18:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("order_plans", sa.Column("currency", sa.String(length=3), server_default="CNY", nullable=False))
    op.add_column("paper_fills", sa.Column("currency", sa.String(length=3), server_default="CNY", nullable=False))
    op.add_column("paper_fills", sa.Column("market_rule_version_id", sa.String(length=64), nullable=True))
    op.add_column("paper_fills", sa.Column("local_trade_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("paper_fills", "local_trade_date")
    op.drop_column("paper_fills", "market_rule_version_id")
    op.drop_column("paper_fills", "currency")
    op.drop_column("order_plans", "currency")
