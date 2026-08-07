from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_session
from app.domain.schemas import (
    ChallengerApproval,
    ChatInput,
    EnableAccountRequest,
    EvidenceInput,
    FeedbackCreate,
    ModelSettingsInput,
)
from app.models import (
    Account,
    AgentRun,
    ChallengerReport,
    DecisionRevision,
    EvidenceRef,
    Experience,
    Instrument,
    LessonCandidate,
    OrderPlan,
    PaperFill,
    ResearchDossier,
    SourceHealth,
    StrategyVersion,
    SystemSetting,
    UserFeedback,
    utc_now,
)
from app.services.agent import SYSTEM_PROMPT, CognitiveAgent
from app.services.audit import append_audit
from app.services.data_sync import HistorySyncService
from app.services.evidence import TrustedEvidenceService
from app.services.evolution import EvolutionService, ExperienceService
from app.services.intraday import IntradayService
from app.services.model import OpenAICompatibleModel
from app.services.portfolio import PortfolioService
from app.services.preflight import PreflightService
from app.services.secrets import SecretStore

router = APIRouter(prefix="/api/v1")
DbSession = Annotated[Session, Depends(get_session)]


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "alpha-sage", "time": utc_now()}


@router.get("/system/status")
def system_status(session: DbSession) -> dict:
    return PortfolioService(session).system_status()


@router.post("/system/preflight")
def preflight(session: DbSession) -> dict:
    return PreflightService(session).run().model_dump(mode="json")


@router.post("/system/enable")
def enable_account(payload: EnableAccountRequest, session: DbSession) -> dict:
    report = PreflightService(session).run()
    if not report.passed:
        raise HTTPException(status_code=409, detail=report.model_dump(mode="json"))
    account = session.scalar(select(Account).where(Account.name == "paper-main"))
    if account is None:
        raise HTTPException(status_code=404, detail="模拟账户不存在")
    account.enabled = True
    account.paused_reason = None
    append_audit(
        session,
        event_type="PAPER_ACCOUNT_ENABLED",
        actor="user",
        entity_type="Account",
        entity_id=account.id,
        payload={"confirmation": payload.confirmation},
    )
    session.commit()
    return {"enabled": True}


@router.post("/system/pause")
def pause_account(session: DbSession, reason: str = "用户手动暂停") -> dict:
    account = session.scalar(select(Account).where(Account.name == "paper-main"))
    if account is None:
        raise HTTPException(status_code=404, detail="模拟账户不存在")
    account.enabled = False
    account.paused_reason = reason
    append_audit(
        session,
        event_type="PAPER_ACCOUNT_PAUSED",
        actor="user",
        entity_type="Account",
        entity_id=account.id,
        payload={"reason": reason},
    )
    session.commit()
    return {"enabled": False, "reason": reason}


def _sync_history_task(years: int, limit: int | None) -> None:
    with SessionLocal() as session:
        asyncio.run(HistorySyncService(session).sync(years=years, limit=limit))


@router.post("/data/sync-history", status_code=202)
def sync_history(
    background: BackgroundTasks,
    years: int = Query(default=5, ge=1, le=10),
    limit: int | None = Query(default=None, ge=1),
) -> dict:
    background.add_task(_sync_history_task, years, limit)
    return {"accepted": True, "years": years, "limit": limit}


@router.get("/data/sources")
def sources(session: DbSession) -> list[dict]:
    return [jsonable_encoder(row) for row in session.scalars(select(SourceHealth).order_by(SourceHealth.source_id))]


@router.post("/evidence/url")
async def ingest_evidence_url(
    url: str,
    session: DbSession,
    instrument_id: str | None = None,
) -> dict:
    instrument = session.get(Instrument, instrument_id) if instrument_id else None
    service = TrustedEvidenceService(session)
    try:
        row = await service.ingest_url(url, instrument=instrument)
        return jsonable_encoder(row)
    finally:
        await service.close()


@router.post("/evidence")
def add_evidence(payload: EvidenceInput, session: DbSession, instrument_id: str | None = None) -> dict:
    instrument = session.get(Instrument, instrument_id) if instrument_id else None
    return jsonable_encoder(TrustedEvidenceService(session).add_structured(payload, instrument))


@router.post("/agent/eod")
def run_eod(session: DbSession, trade_date: date | None = None) -> dict:
    return jsonable_encoder(CognitiveAgent(session).run_eod(trade_date or date.today()))


@router.post("/agent/intraday")
async def run_intraday(session: DbSession, trade_date: date | None = None) -> dict:
    return jsonable_encoder(await IntradayService(session).run(trade_date))


@router.post("/agent/attribute")
def attribute(session: DbSession, as_of: date | None = None) -> dict:
    rows = ExperienceService(session).attribute_due(as_of)
    return {"created": len(rows), "ids": [row.id for row in rows]}


@router.get("/agent/runs")
def runs(session: DbSession, limit: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    rows = session.scalars(select(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit))
    return [jsonable_encoder(row) for row in rows]


@router.get("/research")
def research(session: DbSession, limit: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    rows = list(session.scalars(select(ResearchDossier).order_by(ResearchDossier.created_at.desc()).limit(limit)))
    instruments = (
        {
            item.id: item
            for item in session.scalars(
                select(Instrument).where(Instrument.id.in_({row.instrument_id for row in rows}))
            )
        }
        if rows
        else {}
    )
    return [
        jsonable_encoder(row)
        | {
            "symbol": instruments[row.instrument_id].symbol,
            "name": instruments[row.instrument_id].name,
        }
        for row in rows
    ]


@router.get("/research/{instrument_id}")
def instrument_research(instrument_id: str, session: DbSession) -> dict:
    dossier = session.scalar(
        select(ResearchDossier)
        .where(ResearchDossier.instrument_id == instrument_id)
        .order_by(ResearchDossier.created_at.desc())
        .limit(1)
    )
    if dossier is None:
        raise HTTPException(status_code=404, detail="尚无研究档案")
    decisions = list(
        session.scalars(
            select(DecisionRevision)
            .where(DecisionRevision.dossier_id == dossier.id)
            .order_by(DecisionRevision.horizon, DecisionRevision.revision)
        )
    )
    evidence = (
        list(session.scalars(select(EvidenceRef).where(EvidenceRef.id.in_(dossier.evidence_ids))))
        if dossier.evidence_ids
        else []
    )
    return {
        "dossier": jsonable_encoder(dossier),
        "decisions": [jsonable_encoder(row) for row in decisions],
        "evidence": [jsonable_encoder(row) for row in evidence],
    }


@router.get("/portfolio")
def portfolio(session: DbSession) -> dict:
    return PortfolioService(session).overview()


@router.get("/orders")
def orders(session: DbSession, limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    rows = list(session.scalars(select(OrderPlan).order_by(OrderPlan.created_at.desc()).limit(limit)))
    instruments = (
        {
            item.id: item
            for item in session.scalars(
                select(Instrument).where(Instrument.id.in_({row.instrument_id for row in rows}))
            )
        }
        if rows
        else {}
    )
    return [
        jsonable_encoder(row)
        | {
            "symbol": instruments[row.instrument_id].symbol,
            "name": instruments[row.instrument_id].name,
        }
        for row in rows
    ]


@router.get("/fills")
def fills(session: DbSession, limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    rows = list(session.scalars(select(PaperFill).order_by(PaperFill.filled_at.desc()).limit(limit)))
    instruments = (
        {
            item.id: item
            for item in session.scalars(
                select(Instrument).where(Instrument.id.in_({row.instrument_id for row in rows}))
            )
        }
        if rows
        else {}
    )
    return [
        jsonable_encoder(row)
        | {
            "symbol": instruments[row.instrument_id].symbol,
            "name": instruments[row.instrument_id].name,
        }
        for row in rows
    ]


@router.get("/experiences")
def experiences(session: DbSession, limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    return [
        jsonable_encoder(row)
        for row in session.scalars(select(Experience).order_by(Experience.outcome_date.desc()).limit(limit))
    ]


@router.get("/experiences/search")
def search_experiences(
    session: DbSession,
    q: str = Query(min_length=1, max_length=120),
    limit: int = Query(default=30, ge=1, le=100),
) -> list[dict]:
    tokens = [token.replace('"', "").strip() for token in q.split() if token.strip()]
    if not tokens:
        return []
    fts_tokens = [token for token in tokens if len(token) >= 3]
    short_tokens = [token for token in tokens if len(token) < 3]
    joins = "JOIN experiences_fts ON e.rowid = experiences_fts.rowid" if fts_tokens else ""
    conditions: list[str] = []
    parameters: dict[str, str | int] = {"limit": limit}
    if fts_tokens:
        conditions.append("experiences_fts MATCH :query")
        parameters["query"] = " AND ".join(f'"{token}"' for token in fts_tokens)
    for index, token in enumerate(short_tokens):
        key = f"short_{index}"
        conditions.append(f"(e.thesis_summary LIKE :{key} OR e.tags LIKE :{key} OR e.market_regime LIKE :{key})")
        parameters[key] = f"%{token}%"
    order = "bm25(experiences_fts)" if fts_tokens else "e.created_at DESC"
    ids = list(
        session.execute(
            text(
                f"SELECT e.id FROM experiences e {joins} WHERE {' AND '.join(conditions)} ORDER BY {order} LIMIT :limit"
            ),
            parameters,
        ).scalars()
    )
    rows = {row.id: row for row in session.scalars(select(Experience).where(Experience.id.in_(ids)))}
    return [jsonable_encoder(rows[item_id]) for item_id in ids if item_id in rows]


@router.get("/lessons")
def lessons(session: DbSession) -> list[dict]:
    return [
        jsonable_encoder(row)
        for row in session.scalars(select(LessonCandidate).order_by(LessonCandidate.created_at.desc()))
    ]


@router.post("/feedback")
def feedback(payload: FeedbackCreate, session: DbSession) -> dict:
    row = UserFeedback(**payload.model_dump())
    session.add(row)
    append_audit(
        session,
        event_type="USER_FEEDBACK_ADDED",
        actor="user",
        entity_type="UserFeedback",
        entity_id=row.id,
        payload={"target_type": row.target_type, "target_id": row.target_id},
    )
    session.commit()
    return jsonable_encoder(row)


@router.get("/feedback")
def feedback_rows(session: DbSession, limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    return [
        jsonable_encoder(row)
        for row in session.scalars(select(UserFeedback).order_by(UserFeedback.created_at.desc()).limit(limit))
    ]


@router.post("/evolution/weekly")
def weekly(session: DbSession) -> dict:
    return jsonable_encoder(EvolutionService(session).generate_weekly_lessons())


@router.post("/evolution/monthly")
def monthly(session: DbSession) -> dict:
    return jsonable_encoder(EvolutionService(session).generate_monthly_challenger())


@router.get("/evolution/challengers")
def challengers(session: DbSession) -> list[dict]:
    result: list[dict] = []
    for row in session.scalars(select(ChallengerReport).order_by(ChallengerReport.created_at.desc())):
        strategy = session.get(StrategyVersion, row.strategy_version_id)
        champion = session.get(StrategyVersion, row.champion_version_id)
        changed_rules = sorted(
            key
            for key in set((strategy.rules if strategy else {}) | (champion.rules if champion else {}))
            if (strategy.rules if strategy else {}).get(key) != (champion.rules if champion else {}).get(key)
        )
        result.append(
            jsonable_encoder(row)
            | {
                "strategy_version": strategy.version if strategy else "MISSING",
                "champion_version": champion.version if champion else "MISSING",
                "differences": {
                    "changed_rules": changed_rules,
                    "prompt_overrides": strategy.prompt_overrides if strategy else {},
                    "evidence_weights": strategy.evidence_weights if strategy else {},
                },
            }
        )
    return result


@router.post("/evolution/challengers/{report_id}/approve")
def approve_challenger(report_id: str, payload: ChallengerApproval, session: DbSession) -> dict:
    try:
        return jsonable_encoder(EvolutionService(session).approve(report_id, payload.reason))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/evolution/rollback")
def rollback(session: DbSession, reason: str) -> dict:
    try:
        return jsonable_encoder(EvolutionService(session).rollback(reason))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/settings/model")
def get_model_settings(session: DbSession) -> dict:
    row = session.get(SystemSetting, "model_settings")
    return (row.value if row else {}) | {"api_key_configured": SecretStore.is_configured()}


@router.put("/settings/model")
def set_model_settings(payload: ModelSettingsInput, session: DbSession) -> dict:
    if payload.api_key:
        SecretStore.set_api_key(payload.api_key)
    row = session.get(SystemSetting, "model_settings")
    value = payload.model_dump(exclude={"api_key"})
    if row is None:
        row = SystemSetting(key="model_settings", value=value)
        session.add(row)
    else:
        row.value = value
        row.updated_at = utc_now()
    session.commit()
    return value | {"api_key_configured": SecretStore.is_configured()}


@router.post("/chat")
def chat(payload: ChatInput, session: DbSession) -> StreamingResponse:
    portfolio_context = PortfolioService(session).overview()
    dossier = None
    if payload.instrument_id:
        dossier = session.scalar(
            select(ResearchDossier)
            .where(ResearchDossier.instrument_id == payload.instrument_id)
            .order_by(ResearchDossier.created_at.desc())
            .limit(1)
        )
    model = OpenAICompatibleModel(session)
    answer = model.complete_text(
        purpose="interactive-chat",
        system=SYSTEM_PROMPT + "\n对话只能解释和发起研究建议，不能启用账户、绕过风控或批准挑战者。",
        user=json.dumps(
            {
                "question": payload.message,
                "portfolio": portfolio_context,
                "latest_research": jsonable_encoder(dossier) if dossier else None,
                "context": payload.context,
            },
            ensure_ascii=False,
            default=str,
        ),
    )
    session.commit()

    async def stream():
        for chunk in answer.splitlines(keepends=True):
            yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
