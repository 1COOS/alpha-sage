from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.temporal import UTCDateTime, to_utc


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    return to_utc(value)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class SourceHealth(Base):
    __tablename__ = "source_health"

    source_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    role: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40))
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("acct"))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    initial_cash: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    cash: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    paused_reason: Mapped[str | None] = mapped_column(Text)
    high_watermark: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class MarketRuleVersion(Base):
    __tablename__ = "market_rule_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("mrule"))
    market: Mapped[str] = mapped_column(String(20), default="CN")
    version: Mapped[str] = mapped_column(String(80), unique=True)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    source_uri: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("market", "exchange", "symbol", name="uq_instrument_identity"),
        Index("ix_instruments_investable", "investable", "asset_type"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ins"))
    market: Mapped[str] = mapped_column(String(20), default="CN")
    exchange: Mapped[str] = mapped_column(String(20))
    symbol: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(160))
    asset_type: Mapped[str] = mapped_column(String(20))
    industry: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    listed_on: Mapped[date | None] = mapped_column(Date)
    lot_size: Mapped[int] = mapped_column(Integer, default=100)
    tick_size: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0.01"))
    median_turnover_20d: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    investable: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusion_reason: Mapped[str | None] = mapped_column(Text)
    source_version: Mapped[str | None] = mapped_column(String(80))
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class MarketCalendarDay(Base):
    __tablename__ = "market_calendar_days"

    market: Mapped[str] = mapped_column(String(20), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(80))
    available_at: Mapped[datetime] = mapped_column(UTCDateTime())


class DataArtifact(Base):
    __tablename__ = "data_artifacts"
    __table_args__ = (Index("ix_artifacts_dataset", "dataset", "sealed_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("art"))
    dataset: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(80))
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    rows: Mapped[int] = mapped_column(Integer)
    min_date: Mapped[date | None] = mapped_column(Date)
    max_date: Mapped[date | None] = mapped_column(Date)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime())
    sealed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TimeCorrection(Base):
    __tablename__ = "time_corrections"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "field_name", name="uq_time_correction_target"),
        Index("ix_time_corrections_target", "entity_type", "entity_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("tc"))
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(64))
    field_name: Mapped[str] = mapped_column(String(80))
    original_value: Mapped[str] = mapped_column(Text)
    canonical_utc: Mapped[datetime] = mapped_column(UTCDateTime())
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_kind_started", "kind", "started_at"),
        Index("ix_agent_runs_status_started", "status", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("run"))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    trigger_source: Mapped[str] = mapped_column(String(32), default="SYSTEM")
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    trade_date: Mapped[date | None] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    stage: Mapped[str | None] = mapped_column(String(80))
    progress_current: Mapped[int | None] = mapped_column(Integer)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    progress_message: Mapped[str | None] = mapped_column(Text)
    blocker: Mapped[str | None] = mapped_column(Text)
    input_versions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EvidenceRef(Base):
    __tablename__ = "evidence_refs"
    __table_args__ = (Index("ix_evidence_instrument_published", "instrument_id", "published_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ev"))
    instrument_id: Mapped[str | None] = mapped_column(ForeignKey("instruments.id"))
    source_id: Mapped[str] = mapped_column(String(80))
    source_uri: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime())
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    credibility: Mapped[str] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64))
    raw_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("data_artifacts.id"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ResearchDossier(Base):
    __tablename__ = "research_dossiers"
    __table_args__ = (Index("ix_dossiers_instrument_created", "instrument_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("dos"))
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"))
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    trade_date: Mapped[date] = mapped_column(Date)
    thesis: Mapped[dict[str, Any]] = mapped_column(JSON)
    opposition: Mapped[dict[str, Any]] = mapped_column(JSON)
    synthesis: Mapped[dict[str, Any]] = mapped_column(JSON)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    strategy_version_id: Mapped[str] = mapped_column(ForeignKey("strategy_versions.id"))
    model_version: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(80))
    data_versions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class DecisionRevision(Base):
    __tablename__ = "decision_revisions"
    __table_args__ = (
        UniqueConstraint("decision_key", "revision", name="uq_decision_revision"),
        Index("ix_decisions_instrument_horizon", "instrument_id", "horizon", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("dec"))
    decision_key: Mapped[str] = mapped_column(String(120))
    revision: Mapped[int] = mapped_column(Integer)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("decision_revisions.id"))
    dossier_id: Mapped[str] = mapped_column(ForeignKey("research_dossiers.id"))
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    horizon: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(20))
    target_weight: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    expected_return_low: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    expected_return_high: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    probability_up: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    confidence: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    holding_days: Mapped[int] = mapped_column(Integer)
    rationale: Mapped[str] = mapped_column(Text)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    trigger_reason: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    strategy_version_id: Mapped[str] = mapped_column(ForeignKey("strategy_versions.id"))
    risk_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class OrderPlan(Base):
    __tablename__ = "order_plans"
    __table_args__ = (Index("ix_orders_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ord"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    decision_id: Mapped[str] = mapped_column(ForeignKey("decision_revisions.id"))
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    horizon: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(10))
    order_type: Mapped[str] = mapped_column(String(20), default="MARKET_NEXT_BAR")
    quantity: Mapped[int] = mapped_column(Integer)
    filled_quantity: Mapped[int] = mapped_column(Integer, default=0)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    status: Mapped[str] = mapped_column(String(32))
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    eligible_after: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PaperFill(Base):
    __tablename__ = "paper_fills"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("fill"))
    order_id: Mapped[str] = mapped_column(ForeignKey("order_plans.id"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    horizon: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[int] = mapped_column(Integer)
    reference_price: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    fill_price: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    commission: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    tax: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    slippage_bps: Mapped[int] = mapped_column(Integer)
    market_rule_version_id: Mapped[str | None] = mapped_column(ForeignKey("market_rule_versions.id"))
    bar_artifact_id: Mapped[str] = mapped_column(ForeignKey("data_artifacts.id"))
    local_trade_date: Mapped[date] = mapped_column(Date)
    filled_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PositionLot(Base):
    __tablename__ = "position_lots"
    __table_args__ = (Index("ix_position_lots_account_instrument", "account_id", "instrument_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("lot"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    horizon: Mapped[str] = mapped_column(String(20))
    opened_fill_id: Mapped[str] = mapped_column(ForeignKey("paper_fills.id"))
    opened_trade_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[int] = mapped_column(Integer)
    remaining_quantity: Mapped[int] = mapped_column(Integer)
    cost_price: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CashLedgerEntry(Base):
    __tablename__ = "cash_ledger"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cash"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    event_type: Mapped[str] = mapped_column(String(40))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    reference_type: Mapped[str] = mapped_column(String(40))
    reference_id: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("snap"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    as_of: Mapped[datetime] = mapped_column(UTCDateTime())
    cash: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    market_value: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    equity: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    drawdown: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    benchmark_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    positions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    risk_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Experience(Base):
    __tablename__ = "experiences"
    __table_args__ = (Index("ix_experience_horizon_outcome", "horizon", "outcome_date"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("exp"))
    decision_id: Mapped[str] = mapped_column(ForeignKey("decision_revisions.id"), unique=True)
    strategy_version_id: Mapped[str] = mapped_column(ForeignKey("strategy_versions.id"))
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    horizon: Mapped[str] = mapped_column(String(20))
    market_regime: Mapped[str] = mapped_column(String(80))
    event_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    thesis_summary: Mapped[str] = mapped_column(Text)
    outcome_date: Mapped[date] = mapped_column(Date)
    net_return: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    benchmark_return: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    excess_return: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    direction_hit: Mapped[bool] = mapped_column(Boolean)
    brier_score: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    max_favorable_excursion: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    max_adverse_excursion: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    attribution: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class LessonCandidate(Base):
    __tablename__ = "lesson_candidates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("lesson"))
    week_ending: Mapped[date] = mapped_column(Date)
    scope: Mapped[str] = mapped_column(String(80))
    hypothesis: Mapped[str] = mapped_column(Text)
    supporting_experience_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    contradicting_experience_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    status: Mapped[str] = mapped_column(String(32), default="PROPOSED")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("strategy"))
    version: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(32))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("strategy_versions.id"))
    rules: Mapped[dict[str, Any]] = mapped_column(JSON)
    prompt_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_weights: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class ChallengerReport(Base):
    __tablename__ = "challenger_reports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("challenger"))
    strategy_version_id: Mapped[str] = mapped_column(ForeignKey("strategy_versions.id"))
    champion_version_id: Mapped[str] = mapped_column(ForeignKey("strategy_versions.id"))
    status: Mapped[str] = mapped_column(String(48))
    replay_case_count: Mapped[int] = mapped_column(Integer, default=0)
    shadow_days: Mapped[int] = mapped_column(Integer, default=0)
    net_excess_return: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)
    champion_excess_return: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)
    max_drawdown: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)
    champion_max_drawdown: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)
    calibration_score: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)
    champion_calibration_score: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)
    hard_risk_violations: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    approved_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class UserFeedback(Base):
    __tablename__ = "user_feedback"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("feedback"))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    sentiment: Mapped[str] = mapped_column(String(20), default="NEUTRAL")
    outcome_note: Mapped[str | None] = mapped_column(Text)
    used_for_challenger: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class ModelInvocation(Base):
    __tablename__ = "model_invocations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("model"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"))
    purpose: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_hash: Mapped[str] = mapped_column(String(64))
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="COMPLETED")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class IntradayTriggerState(Base):
    __tablename__ = "intraday_trigger_states"
    __table_args__ = (UniqueConstraint("trade_date", "instrument_id", name="uq_trigger_day_symbol"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("trigger"))
    trade_date: Mapped[date] = mapped_column(Date)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    last_called_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    last_reason: Mapped[str | None] = mapped_column(Text)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("audit"))
    event_type: Mapped[str] = mapped_column(String(80))
    actor: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
