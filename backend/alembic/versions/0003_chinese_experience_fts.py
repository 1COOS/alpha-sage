"""Chinese experience FTS

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-06 18:24:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS experiences_fts_insert")
    op.execute("DROP TABLE IF EXISTS experiences_fts")
    op.execute(
        "CREATE VIRTUAL TABLE experiences_fts USING fts5("
        "thesis_summary, tags, market_regime, content='experiences', content_rowid='rowid', tokenize='trigram')"
    )
    op.execute(
        "INSERT INTO experiences_fts(rowid, thesis_summary, tags, market_regime) "
        "SELECT rowid, thesis_summary, tags, market_regime FROM experiences"
    )
    op.execute(
        "CREATE TRIGGER experiences_fts_insert AFTER INSERT ON experiences BEGIN "
        "INSERT INTO experiences_fts(rowid, thesis_summary, tags, market_regime) "
        "VALUES (new.rowid, new.thesis_summary, new.tags, new.market_regime); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS experiences_fts_insert")
    op.execute("DROP TABLE IF EXISTS experiences_fts")
    op.execute(
        "CREATE VIRTUAL TABLE experiences_fts USING fts5("
        "thesis_summary, tags, market_regime, content='experiences', content_rowid='rowid')"
    )
    op.execute(
        "INSERT INTO experiences_fts(rowid, thesis_summary, tags, market_regime) "
        "SELECT rowid, thesis_summary, tags, market_regime FROM experiences"
    )
    op.execute(
        "CREATE TRIGGER experiences_fts_insert AFTER INSERT ON experiences BEGIN "
        "INSERT INTO experiences_fts(rowid, thesis_summary, tags, market_regime) "
        "VALUES (new.rowid, new.thesis_summary, new.tags, new.market_regime); END"
    )
