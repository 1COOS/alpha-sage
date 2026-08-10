from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import DecisionAction, Horizon
from app.temporal import to_utc


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class EvidenceInput(BaseModel):
    source_id: str
    source_uri: str
    title: str
    excerpt: str
    published_at: datetime
    credibility: Literal["OFFICIAL", "HIGH", "MEDIUM", "LOW"]

    @field_validator("published_at")
    @classmethod
    def validate_published_at_timezone(cls, value: datetime) -> datetime:
        return to_utc(value)


class HorizonView(BaseModel):
    horizon: Horizon
    action: DecisionAction
    target_weight: Decimal = Field(ge=0, le=0.10)
    expected_return_low: Decimal
    expected_return_high: Decimal
    probability_up: Decimal = Field(ge=0, le=1)
    confidence: Decimal = Field(ge=0, le=1)
    holding_days: int = Field(ge=1, le=250)
    rationale: str = Field(min_length=10)
    risks: list[str] = Field(default_factory=list)

    @field_validator("holding_days")
    @classmethod
    def validate_horizon_days(cls, value: int, info):
        horizon = info.data.get("horizon")
        bounds = {
            Horizon.SHORT: (1, 5),
            Horizon.SWING: (6, 30),
            Horizon.LONG: (31, 250),
        }
        if horizon in bounds:
            lower, upper = bounds[horizon]
            if not lower <= value <= upper:
                raise ValueError(f"{horizon} holding_days must be {lower}-{upper}")
        return value


class ThesisOutput(BaseModel):
    summary: str
    catalysts: list[str]
    supporting_claims: list[str]
    horizon_views: list[HorizonView]


class OppositionOutput(BaseModel):
    strongest_counter_thesis: str
    failure_modes: list[str]
    evidence_gaps: list[str]
    horizon_objections: dict[str, list[str]]


class SynthesisOutput(BaseModel):
    verdict: Literal["INVEST", "WATCH", "REJECT"]
    summary: str
    material_new_evidence_required_for_long_reversal: list[str]
    horizon_views: list[HorizonView]


class ResearchBundle(BaseModel):
    instrument_id: str
    symbol: str
    trade_date: date
    thesis: ThesisOutput
    opposition: OppositionOutput
    synthesis: SynthesisOutput
    evidence_ids: list[str]


class PortfolioAllocation(BaseModel):
    instrument_id: str
    horizon: Horizon
    target_weight: Decimal = Field(ge=0, le=0.10)
    reason: str


class PortfolioProposalInput(BaseModel):
    allocations: list[PortfolioAllocation]
    cash_weight: Decimal = Field(ge=0.15, le=1)
    market_regime: str
    rationale: str


class QuoteObservation(BaseModel):
    instrument_id: str
    observed_at: datetime
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Decimal
    provider: str
    available_at: datetime


class PreflightCheck(BaseModel):
    key: str
    passed: bool
    detail: str


class PreflightReport(BaseModel):
    passed: bool
    checks: list[PreflightCheck]


class EnableAccountRequest(BaseModel):
    confirmation: Literal["ENABLE PAPER ACCOUNT"]


class FeedbackCreate(BaseModel):
    target_type: str
    target_id: str | None = None
    content: str = Field(min_length=2)
    sentiment: Literal["POSITIVE", "NEUTRAL", "NEGATIVE"] = "NEUTRAL"


class ModelSettingsInput(BaseModel):
    base_url: str
    api_mode: Literal["responses", "chat_completions"] = "responses"
    reasoning_model: str
    fast_model: str
    daily_request_budget: int = Field(default=100, ge=1, le=1000)
    api_key: str | None = Field(default=None, repr=False)


class ChatInput(BaseModel):
    message: str = Field(min_length=1)
    instrument_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class ChallengerApproval(BaseModel):
    reason: str = Field(min_length=5)


class LessonOutput(BaseModel):
    scope: str
    hypothesis: str
    supporting_experience_ids: list[str]
    contradicting_experience_ids: list[str]
    confidence: Decimal = Field(ge=0, le=1)


class WeeklyLessonsOutput(BaseModel):
    lessons: list[LessonOutput]


class ChallengerCandidateOutput(BaseModel):
    name: str
    rule_changes: dict[str, Any]
    prompt_overrides: dict[str, Any]
    evidence_weights: dict[str, Decimal]
    rationale: str


class ReplayPrediction(BaseModel):
    experience_id: str
    action: DecisionAction
    probability_up: Decimal = Field(ge=0, le=1)


class ReplayPredictionsOutput(BaseModel):
    predictions: list[ReplayPrediction]


class SystemStatus(ORMModel):
    account_enabled: bool
    account_cash: Decimal
    equity: Decimal
    drawdown: Decimal
    current_strategy: str
    last_run: dict[str, Any] | None
    source_health: list[dict[str, Any]]
    blockers: list[str]
