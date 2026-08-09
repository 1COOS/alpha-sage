from pathlib import Path


def test_initial_migration_contains_append_only_and_fts_guards():
    migration = Path(__file__).parents[1] / "alembic" / "versions" / "0001_initial.py"
    text = migration.read_text(encoding="utf-8")
    assert "experiences_fts" in text
    assert "decision_revisions_no_update" not in text
    assert "immutable_tables" in text
    assert "append-only table cannot be updated" in text


def test_execution_trace_migration_adds_currency_trade_date_and_rule_version():
    migration = Path(__file__).parents[1] / "alembic" / "versions" / "0002_market_execution_trace.py"
    text = migration.read_text(encoding="utf-8")
    assert '"currency"' in text
    assert '"local_trade_date"' in text
    assert '"market_rule_version_id"' in text


def test_chinese_experience_search_uses_trigram_tokenizer():
    migration = Path(__file__).parents[1] / "alembic" / "versions" / "0003_chinese_experience_fts.py"
    text = migration.read_text(encoding="utf-8")
    assert "tokenize='trigram'" in text
    assert "SELECT rowid, thesis_summary, tags, market_regime FROM experiences" in text


def test_run_feedback_migration_adds_progress_and_failed_model_audit_fields():
    migration = Path(__file__).parents[1] / "alembic" / "versions" / "0004_run_feedback.py"
    text = migration.read_text(encoding="utf-8")
    for field in (
        "trigger_source",
        "parameters",
        "updated_at",
        "stage",
        "progress_current",
        "progress_total",
        "progress_message",
    ):
        assert field in text
    for field in ("status", "latency_ms", "http_status", "error_type", "error_message"):
        assert field in text
    assert "ix_agent_runs_status_started" in text
