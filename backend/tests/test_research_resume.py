from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.domain.enums import RunKind, RunStatus, StrategyStatus
from app.domain.schemas import PortfolioProposalInput, ResearchBundle, ThesisOutput
from app.models import (
    Account,
    AgentRun,
    DecisionRevision,
    Instrument,
    ResearchDossier,
    ResearchPhaseCheckpoint,
    StrategyVersion,
)
from app.services.agent import CognitiveAgent
from app.services.model import FunctionModel
from app.services.run_queue import RunProgressReporter


def _view(horizon: str, days: int) -> dict:
    return {
        "horizon": horizon,
        "action": "WATCH",
        "target_weight": "0",
        "expected_return_low": "-0.1",
        "expected_return_high": "0.1",
        "probability_up": "0.5",
        "confidence": "0.5",
        "holding_days": days,
        "rationale": "证据不足时保持现金并等待新的可验证信息",
        "risks": ["证据不足"],
    }


def _views() -> list[dict]:
    return [_view("SHORT", 3), _view("SWING", 15), _view("LONG", 90)]


def _thesis() -> dict:
    return {
        "summary": "基于当前证据形成的正方研究结论",
        "catalysts": ["出现新的可信证据"],
        "supporting_claims": ["当前数据只支持谨慎观察"],
        "horizon_views": _views(),
    }


def _opposition() -> dict:
    return {
        "strongest_counter_thesis": "现有信息不足以证明趋势能够持续",
        "failure_modes": ["催化未兑现"],
        "evidence_gaps": ["缺少基本面证据"],
        "horizon_objections": {"SHORT": ["波动风险"]},
    }


def _synthesis() -> dict:
    return {
        "verdict": "WATCH",
        "summary": "综合正反观点后继续等待可信证据",
        "material_new_evidence_required_for_long_reversal": ["正式财务证据"],
        "horizon_views": _views(),
    }


def test_research_schema_exposes_horizon_specific_ranges_and_requires_each_horizon_once():
    schema = ThesisOutput.model_json_schema()
    definitions = schema["$defs"]
    assert definitions["ShortHorizonView"]["properties"]["holding_days"] == {
        "maximum": 5,
        "minimum": 1,
        "title": "Holding Days",
        "type": "integer",
    }
    assert definitions["SwingHorizonView"]["properties"]["holding_days"]["minimum"] == 6
    assert definitions["SwingHorizonView"]["properties"]["holding_days"]["maximum"] == 30
    assert definitions["LongHorizonView"]["properties"]["holding_days"]["minimum"] == 31
    assert schema["properties"]["horizon_views"]["minItems"] == 3
    assert schema["properties"]["horizon_views"]["maxItems"] == 3

    invalid_days = _thesis()
    invalid_days["horizon_views"][0]["holding_days"] = 10
    with pytest.raises(ValidationError, match="less than or equal to 5"):
        ThesisOutput.model_validate(invalid_days)

    duplicate = _thesis()
    duplicate["horizon_views"][1] = _view("SHORT", 2)
    with pytest.raises(ValidationError, match="exactly one SHORT, SWING, and LONG"):
        ThesisOutput.model_validate(duplicate)


def test_resume_reuses_only_valid_matching_phase_checkpoints(session):
    instrument = Instrument(
        exchange="SSE",
        symbol="600001",
        name="续跑测试",
        asset_type="STOCK",
        industry="测试",
        investable=True,
    )
    parent = AgentRun(kind=RunKind.EOD, status=RunStatus.RUNNING, trade_date=date.today())
    session.add_all([instrument, parent])
    session.commit()
    champion = session.scalar(select(StrategyVersion).where(StrategyVersion.status == StrategyStatus.CHAMPION))

    def parent_handler(purpose, _schema):
        if purpose == "research-thesis":
            return _thesis()
        raise RuntimeError("opposition interrupted")

    parent_agent = CognitiveAgent(session, FunctionModel(parent_handler))
    with pytest.raises(RuntimeError, match="interrupted"):
        parent_agent._research_instrument(parent, instrument, date.today(), "NEUTRAL", champion)
    parent.status = RunStatus.FAILED
    session.commit()
    assert session.scalar(select(func.count()).select_from(ResearchPhaseCheckpoint)) == 1

    child = AgentRun(
        kind=RunKind.EOD,
        status=RunStatus.RUNNING,
        trade_date=date.today(),
        resumed_from_run_id=parent.id,
    )
    session.add(child)
    session.commit()
    child_calls: list[str] = []

    def child_handler(purpose, _schema):
        child_calls.append(purpose)
        if purpose == "research-opposition":
            return _opposition()
        if purpose == "research-synthesis":
            return _synthesis()
        raise AssertionError(f"checkpoint should have been reused: {purpose}")

    child_agent = CognitiveAgent(session, FunctionModel(child_handler))
    bundle = child_agent._research_instrument(child, instrument, date.today(), "NEUTRAL", champion)

    assert bundle.synthesis.verdict == "WATCH"
    assert child_calls == ["research-opposition", "research-synthesis"]
    child_checkpoints = list(
        session.scalars(
            select(ResearchPhaseCheckpoint)
            .where(ResearchPhaseCheckpoint.run_id == child.id)
            .order_by(ResearchPhaseCheckpoint.created_at)
        )
    )
    assert [item.checkpoint_key for item in child_checkpoints] == ["THESIS", "OPPOSITION", "SYNTHESIS"]
    assert child_checkpoints[0].source_checkpoint_id is not None
    assert child_checkpoints[1].source_checkpoint_id is None
    assert session.get(AgentRun, parent.id).status == RunStatus.FAILED

    changed = AgentRun(
        kind=RunKind.EOD,
        status=RunStatus.RUNNING,
        trade_date=date.today(),
        resumed_from_run_id=parent.id,
    )
    session.add(changed)
    session.commit()
    changed_calls: list[str] = []

    class ChangedModel(FunctionModel):
        model_name = "function-model-v2"

    def changed_handler(purpose, _schema):
        changed_calls.append(purpose)
        return {
            "research-thesis": _thesis(),
            "research-opposition": _opposition(),
            "research-synthesis": _synthesis(),
        }[purpose]

    changed_agent = CognitiveAgent(session, ChangedModel(changed_handler))
    changed_agent._research_instrument(changed, instrument, date.today(), "NEUTRAL", champion)
    changed_checkpoints = list(
        session.scalars(select(ResearchPhaseCheckpoint).where(ResearchPhaseCheckpoint.run_id == changed.id))
    )
    assert changed_calls == ["research-thesis", "research-opposition", "research-synthesis"]
    assert all(item.source_checkpoint_id is None for item in changed_checkpoints)


def test_persisting_failure_rolls_back_business_facts_but_keeps_checkpoint(session, monkeypatch):
    account = session.scalar(select(Account).where(Account.name == "paper-main"))
    account.enabled = True
    instrument = Instrument(
        exchange="SSE",
        symbol="600002",
        name="原子事务测试",
        asset_type="STOCK",
        industry="测试",
        investable=True,
    )
    run = AgentRun(kind=RunKind.EOD, status=RunStatus.RUNNING, trade_date=date.today())
    session.add_all([instrument, run])
    session.commit()
    bundle = ResearchBundle(
        instrument_id=instrument.id,
        symbol=instrument.symbol,
        trade_date=date.today(),
        thesis=_thesis(),
        opposition=_opposition(),
        synthesis=_synthesis(),
        evidence_ids=[],
    )
    agent = CognitiveAgent(session, FunctionModel(lambda purpose, schema: None))
    monkeypatch.setattr(
        "app.services.agent.PreflightService.run",
        lambda _self: SimpleNamespace(passed=True, checks=[]),
    )
    monkeypatch.setattr(agent, "_market_regime", lambda _trade_date: "NEUTRAL")
    monkeypatch.setattr(agent, "_discover_opportunities", lambda _trade_date, limit: [instrument])

    def research_with_checkpoint(*_args, **_kwargs):
        session.add(
            ResearchPhaseCheckpoint(
                run_id=run.id,
                instrument_id=instrument.id,
                trade_date=date.today(),
                checkpoint_key="SYNTHESIS",
                input_hash="a" * 64,
                output_hash="b" * 64,
                output_json=_synthesis(),
                provider="injected",
                model_version="function-model",
                prompt_version=agent.prompt_version,
                schema_version=agent.research_schema_version,
                strategy_version="alpha-sage-cognition-v1",
            )
        )
        session.commit()
        return bundle

    monkeypatch.setattr(agent, "_research_instrument", research_with_checkpoint)
    monkeypatch.setattr(
        agent,
        "_portfolio_proposal",
        lambda *_args: PortfolioProposalInput(
            allocations=[],
            cash_weight="1",
            market_regime="NEUTRAL",
            rationale="没有满足条件的配置时保留全部现金",
        ),
    )
    monkeypatch.setattr(agent.risk, "enforce_drawdown_target", lambda proposal, _state: proposal)
    monkeypatch.setattr(
        agent.risk,
        "validate_proposal",
        lambda _proposal: SimpleNamespace(passed=True, blockers=[], warnings=[]),
    )
    monkeypatch.setattr(agent, "_prepare_shadow_decisions", lambda *_args: [])
    monkeypatch.setattr(agent.market, "price_map", lambda _instruments: {})
    monkeypatch.setattr(
        agent.risk,
        "build_orders",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("persist failed")),
    )

    result = agent.run_eod(date.today(), run=run, reporter=RunProgressReporter(session, run))

    assert result.status == RunStatus.FAILED
    assert result.stage == "PERSISTING"
    assert session.scalar(select(func.count()).select_from(ResearchPhaseCheckpoint)) == 1
    assert session.scalar(select(func.count()).select_from(ResearchDossier)) == 0
    assert session.scalar(select(func.count()).select_from(DecisionRevision)) == 0
