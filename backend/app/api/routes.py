from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.domain.enums import RunKind
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
from app.services.data_sync import HistorySyncProgress, HistorySyncService
from app.services.evidence import TrustedEvidenceService
from app.services.evolution import EvolutionService, ExperienceService
from app.services.intraday import IntradayService
from app.services.model import OpenAICompatibleModel, resolve_model_settings
from app.services.model_test import run_model_connection_test
from app.services.portfolio import PortfolioService
from app.services.preflight import PreflightService
from app.services.run_queue import RUN_QUEUE, ActiveRunConflict, RunProgressReporter
from app.services.secrets import SecretStore
from app.temporal import api_jsonable, beijing_today

router = APIRouter(prefix="/api/v1")
DbSession = Annotated[Session, Depends(get_session)]


def _accepted(run: AgentRun) -> dict:
    return {
        "run_id": run.id,
        "kind": run.kind,
        "status": run.status,
        "stage": run.stage,
        "message": run.progress_message,
    }


def _submit_or_conflict(**kwargs) -> AgentRun:
    try:
        return RUN_QUEUE.submit(**kwargs)
    except ActiveRunConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "run_id": exc.run.id, "kind": exc.run.kind, "status": exc.run.status},
        ) from exc


@router.get("/health")
def health() -> dict:
    return api_jsonable({"status": "ok", "service": "alpha-sage", "time": utc_now()})


@router.get("/system/status")
def system_status(session: DbSession) -> dict:
    return api_jsonable(PortfolioService(session).system_status())


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


@router.post("/data/sync-history", status_code=202)
def sync_history(
    years: int = Query(default=5, ge=1, le=10),
    limit: int | None = Query(default=None, ge=1),
) -> dict:
    def job(session: Session, run: AgentRun, reporter: RunProgressReporter) -> AgentRun:
        def progress(item: HistorySyncProgress) -> None:
            reporter.update(
                item.phase.upper(),
                item.detail,
                current=item.current,
                total=item.total,
            )

        return asyncio.run(HistorySyncService(session, progress=progress).sync(years=years, limit=limit, run=run))

    run = _submit_or_conflict(
        kind=RunKind.DATA_SYNC,
        trigger_source="MANUAL",
        parameters={"years": years, "limit": limit},
        job=job,
    )
    return _accepted(run)


@router.get("/data/sources")
def sources(session: DbSession) -> list[dict]:
    return api_jsonable(list(session.scalars(select(SourceHealth).order_by(SourceHealth.source_id))))


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
        return api_jsonable(row)
    finally:
        await service.close()


@router.post("/evidence")
def add_evidence(payload: EvidenceInput, session: DbSession, instrument_id: str | None = None) -> dict:
    instrument = session.get(Instrument, instrument_id) if instrument_id else None
    return api_jsonable(TrustedEvidenceService(session).add_structured(payload, instrument))


@router.post("/agent/eod", status_code=202)
def run_eod(trade_date: date | None = None) -> dict:
    resolved = trade_date or beijing_today()

    def job(session: Session, run: AgentRun, reporter: RunProgressReporter) -> AgentRun:
        return CognitiveAgent(session).run_eod(resolved, run=run, reporter=reporter)

    run = _submit_or_conflict(
        kind=RunKind.EOD,
        trigger_source="MANUAL",
        parameters={"trade_date": resolved.isoformat()},
        trade_date=resolved,
        job=job,
    )
    return _accepted(run)


@router.post("/agent/intraday", status_code=202)
def run_intraday(trade_date: date | None = None) -> dict:
    resolved = trade_date or beijing_today()

    def job(session: Session, run: AgentRun, reporter: RunProgressReporter) -> AgentRun:
        return asyncio.run(IntradayService(session).run(resolved, run=run, reporter=reporter))

    run = _submit_or_conflict(
        kind=RunKind.INTRADAY,
        trigger_source="MANUAL",
        parameters={"trade_date": resolved.isoformat()},
        trade_date=resolved,
        job=job,
    )
    return _accepted(run)


@router.post("/agent/attribute", status_code=202)
def attribute(as_of: date | None = None) -> dict:
    resolved = as_of or beijing_today()

    def job(session: Session, run: AgentRun, reporter: RunProgressReporter) -> AgentRun:
        reporter.update("ATTRIBUTING", "计算到期决策的真实结果与归因")
        rows = ExperienceService(session).attribute_due(resolved)
        return reporter.complete(
            {"created": len(rows), "ids": [row.id for row in rows]},
            f"归因完成，新增 {len(rows)} 条经验",
        )

    run = _submit_or_conflict(
        kind=RunKind.ATTRIBUTION,
        trigger_source="MANUAL",
        parameters={"as_of": resolved.isoformat()},
        trade_date=resolved,
        job=job,
    )
    return _accepted(run)


@router.get("/agent/runs")
def runs(
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = None,
    kind: str | None = None,
) -> list[dict]:
    query = select(AgentRun)
    if status:
        query = query.where(AgentRun.status == status)
    if kind:
        query = query.where(AgentRun.kind == kind)
    rows = session.scalars(query.order_by(AgentRun.started_at.desc()).limit(limit))
    return api_jsonable(list(rows))


@router.get("/agent/runs/{run_id}")
def run_detail(run_id: str, session: DbSession) -> dict:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return api_jsonable(run)


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
        api_jsonable(row)
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
        "dossier": api_jsonable(dossier),
        "decisions": [api_jsonable(row) for row in decisions],
        "evidence": [api_jsonable(row) for row in evidence],
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
        api_jsonable(row)
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
        api_jsonable(row)
        | {
            "symbol": instruments[row.instrument_id].symbol,
            "name": instruments[row.instrument_id].name,
        }
        for row in rows
    ]


@router.get("/experiences")
def experiences(session: DbSession, limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    return [
        api_jsonable(row)
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
    return [api_jsonable(rows[item_id]) for item_id in ids if item_id in rows]


@router.get("/lessons")
def lessons(session: DbSession) -> list[dict]:
    return [
        api_jsonable(row)
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
    return api_jsonable(row)


@router.get("/feedback")
def feedback_rows(session: DbSession, limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    return [
        api_jsonable(row)
        for row in session.scalars(select(UserFeedback).order_by(UserFeedback.created_at.desc()).limit(limit))
    ]


@router.post("/evolution/weekly", status_code=202)
def weekly() -> dict:
    resolved = beijing_today()

    def job(session: Session, run: AgentRun, reporter: RunProgressReporter) -> AgentRun:
        return EvolutionService(session).generate_weekly_lessons(resolved, run=run, reporter=reporter)

    run = _submit_or_conflict(
        kind=RunKind.WEEKLY,
        trigger_source="MANUAL",
        parameters={"week_ending": resolved.isoformat()},
        trade_date=resolved,
        job=job,
    )
    return _accepted(run)


@router.post("/evolution/monthly", status_code=202)
def monthly() -> dict:
    resolved = beijing_today()

    def job(session: Session, run: AgentRun, reporter: RunProgressReporter) -> AgentRun:
        return EvolutionService(session).generate_monthly_challenger(resolved, run=run, reporter=reporter)

    run = _submit_or_conflict(
        kind=RunKind.MONTHLY,
        trigger_source="MANUAL",
        parameters={"as_of": resolved.isoformat()},
        trade_date=resolved,
        job=job,
    )
    return _accepted(run)


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
            api_jsonable(row)
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
        return api_jsonable(EvolutionService(session).approve(report_id, payload.reason))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/evolution/rollback")
def rollback(session: DbSession, reason: str) -> dict:
    try:
        return api_jsonable(EvolutionService(session).rollback(reason))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/settings/model")
def get_model_settings(session: DbSession) -> dict:
    return resolve_model_settings(session, require_api_key=False).public_dict()


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
    return resolve_model_settings(session, require_api_key=False).public_dict()


@router.post("/settings/model/test", status_code=202)
def test_model_settings(payload: ModelSettingsInput) -> dict:
    candidate = payload.model_dump(exclude={"api_key"})
    transient_key = payload.api_key.strip() if payload.api_key else None

    def job(session: Session, run: AgentRun, reporter: RunProgressReporter) -> AgentRun:
        return run_model_connection_test(
            session,
            run,
            reporter,
            candidate=candidate,
            api_key_override=transient_key,
        )

    run = _submit_or_conflict(
        kind=RunKind.MODEL_TEST,
        trigger_source="MANUAL",
        parameters=candidate | {"api_key_supplied": bool(transient_key)},
        job=job,
    )
    return _accepted(run)


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
                "latest_research": api_jsonable(dossier) if dossier else None,
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
