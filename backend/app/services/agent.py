from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import (
    DecisionAction,
    Horizon,
    RunKind,
    RunStatus,
    StrategyStatus,
)
from app.domain.schemas import (
    OppositionOutput,
    PortfolioProposalInput,
    ResearchBundle,
    SynthesisOutput,
    ThesisOutput,
)
from app.models import (
    Account,
    AgentRun,
    DecisionRevision,
    EvidenceRef,
    Experience,
    Instrument,
    PositionLot,
    ResearchDossier,
    StrategyVersion,
    utc_now,
)
from app.services.audit import append_audit
from app.services.market_repository import MarketRepository
from app.services.model import OpenAICompatibleModel, StructuredModel, format_run_failure
from app.services.preflight import PreflightService
from app.services.risk import RiskEngine
from app.services.run_queue import RunProgressReporter
from app.temporal import SHANGHAI

SYSTEM_PROMPT = """你是 Alpha Sage，一个证据优先、会承认不确定性的投资认知 Agent。
你不是传统因子打分器。你的职责是形成可证伪论点、主动寻找反证、区分三个投资周期，
并允许现金作为合法结果。不得编造证据，不得绕过仓位、数据、交易和版本规则。
所有结论必须基于输入中决策时点已经可获得的资料。"""


class CognitiveAgent:
    prompt_version = "cognition-v1"

    def __init__(self, session: Session, model: StructuredModel | None = None):
        self.session = session
        self.model_was_injected = model is not None
        self._model = model
        self.market = MarketRepository(session)
        self.risk = RiskEngine(session)

    @property
    def model(self) -> StructuredModel:
        if self._model is None:
            self._model = OpenAICompatibleModel(self.session)
        return self._model

    def run_eod(
        self,
        trade_date: date,
        *,
        run: AgentRun | None = None,
        reporter: RunProgressReporter | None = None,
    ) -> AgentRun:
        if run is None:
            run = AgentRun(kind=RunKind.EOD, status=RunStatus.RUNNING, trade_date=trade_date)
            self.session.add(run)
            self.session.commit()
        run.trade_date = trade_date
        try:
            if reporter:
                reporter.update("PREFLIGHT", "检查账户、数据、模型和交易规则")
            account = self.session.scalar(select(Account).where(Account.name == "paper-main"))
            if account is None or not account.enabled:
                return self._block(run, "模拟账户尚未人工启用")
            from app.services.portfolio import PortfolioService

            portfolio_state = PortfolioService(self.session).overview()
            if portfolio_state["risk_state"] == "PAUSE_NEW_ORDERS":
                return self._block(run, "组合回撤达到18%，已暂停新开仓")
            preflight = PreflightService(
                self.session,
                model_ready=True if self.model_was_injected else None,
            ).run()
            if not preflight.passed:
                return self._block(
                    run,
                    "; ".join(check.detail for check in preflight.checks if not check.passed),
                )
            champion = self.session.scalar(
                select(StrategyVersion).where(StrategyVersion.status == StrategyStatus.CHAMPION)
            )
            if champion is None:
                return self._block(run, "缺少活动冠军策略")
            if reporter:
                reporter.update("OPPORTUNITY_DISCOVERY", "识别具备时点数据的研究机会")
            market_regime = self._market_regime(trade_date)
            opportunities = self._discover_opportunities(trade_date, limit=20)
            if not opportunities:
                return self._block(run, "没有具备时点数据的可研究机会；保持现金")
            targets = opportunities[:8]
            research = []
            for index, instrument in enumerate(targets, start=1):
                research.append(
                    self._research_instrument(
                        run,
                        instrument,
                        trade_date,
                        market_regime,
                        champion,
                        reporter=reporter,
                        index=index,
                        total=len(targets),
                    )
                )
            if reporter:
                reporter.update(
                    "PORTFOLIO_ALLOCATION",
                    "综合研究结果并生成目标组合",
                    current=len(targets) * 3,
                    total=len(targets) * 3 + 1,
                )
            proposal = self._portfolio_proposal(run, research, market_regime)
            proposal = self.risk.enforce_drawdown_target(proposal, portfolio_state["risk_state"])
            research_by_instrument = {item.instrument_id: item for item in research}
            held_ids = set(
                self.session.scalars(
                    select(PositionLot.instrument_id).where(
                        PositionLot.account_id == account.id,
                        PositionLot.remaining_quantity > 0,
                    )
                )
            )
            missing_evidence = [
                allocation.instrument_id
                for allocation in proposal.allocations
                if allocation.instrument_id not in held_ids
                and (
                    allocation.instrument_id not in research_by_instrument
                    or not research_by_instrument[allocation.instrument_id].evidence_ids
                )
            ]
            if missing_evidence:
                return self._block(run, f"新开仓缺少可信证据引用：{', '.join(missing_evidence)}")
            if reporter:
                reporter.update("RISK_VALIDATION", "执行不可绕过的组合硬风控")
            risk = self.risk.validate_proposal(proposal)
            if not risk.passed:
                return self._block(run, "组合提案未通过硬风控：" + "; ".join(risk.blockers))
            if reporter:
                reporter.update("PERSISTING", "保存研究、决策和模拟订单")
            decisions = self._persist_research(run, research, champion)
            from app.services.evolution import EvolutionService

            shadow_decision_count = EvolutionService(self.session, self.model).create_shadow_decisions(run)
            instruments = list(
                self.session.scalars(
                    select(Instrument).where(
                        Instrument.id.in_({allocation.instrument_id for allocation in proposal.allocations})
                    )
                )
            )
            prices = self.market.price_map(instruments)
            plans = self.risk.build_orders(
                account=account,
                proposal=proposal,
                decisions=decisions,
                prices=prices,
            )
            self.session.add_all(plans)
            run.status = RunStatus.COMPLETED
            run.finished_at = utc_now()
            run.updated_at = run.finished_at
            run.stage = "COMPLETED"
            run.progress_current = len(targets) * 3 + 1
            run.progress_total = len(targets) * 3 + 1
            run.progress_message = "盘后研究完成"
            run.input_versions = {
                "strategy": champion.version,
                "prompt": self.prompt_version,
                "model": self.model.model_name,
                "risk": self.risk.version,
            }
            run.result = {
                "market_regime": market_regime,
                "opportunity_count": len(opportunities),
                "researched_count": len(research),
                "decision_count": len(decisions),
                "shadow_decision_count": shadow_decision_count,
                "order_count": len(plans),
                "cash_weight": str(proposal.cash_weight),
                "risk_warnings": risk.warnings,
            }
            append_audit(
                self.session,
                event_type="EOD_AGENT_COMPLETED",
                actor="alpha-sage",
                entity_type="AgentRun",
                entity_id=run.id,
                payload=run.result,
            )
            self.session.commit()
            return run
        except Exception as exc:  # noqa: BLE001 - failures are persisted as run evidence
            failed_stage = run.stage
            message, result = format_run_failure(
                self.session,
                run_id=run.id,
                stage=failed_stage,
                exc=exc,
            )
            run.status = RunStatus.FAILED
            run.blocker = message
            run.finished_at = utc_now()
            run.updated_at = run.finished_at
            run.stage = failed_stage or "FAILED"
            run.progress_message = message
            run.result = result
            self.session.commit()
            return run

    def revise_intraday(
        self,
        *,
        run: AgentRun,
        instrument: Instrument,
        trigger_reason: str,
        bar_context: dict[str, Any],
        has_new_material_evidence: bool,
    ) -> list[DecisionRevision]:
        latest_decisions = list(
            self.session.scalars(
                select(DecisionRevision)
                .where(DecisionRevision.instrument_id == instrument.id)
                .order_by(DecisionRevision.created_at.desc())
            )
        )
        by_horizon: dict[str, DecisionRevision] = {}
        for decision in latest_decisions:
            by_horizon.setdefault(decision.horizon, decision)
        if not by_horizon:
            return []
        prompt = {
            "instrument": {"symbol": instrument.symbol, "name": instrument.name},
            "trigger": trigger_reason,
            "bar": bar_context,
            "new_material_evidence": has_new_material_evidence,
            "current_decisions": [
                {
                    "horizon": item.horizon,
                    "action": item.action,
                    "target_weight": str(item.target_weight),
                    "rationale": item.rationale,
                }
                for item in by_horizon.values()
            ],
            "rule": "中长线不得只因短时价格变化反转方向；没有新证据时应保持或仅降低确信度。",
        }
        synthesis = self.model.complete_json(
            purpose="intraday-revision",
            system=SYSTEM_PROMPT,
            user=json.dumps(prompt, ensure_ascii=False, default=str),
            schema=SynthesisOutput,
            run_id=run.id,
        )
        revisions: list[DecisionRevision] = []
        for view in synthesis.horizon_views:
            previous = by_horizon.get(view.horizon)
            if previous is None:
                continue
            if (
                view.horizon == Horizon.LONG
                and not has_new_material_evidence
                and self._direction_reversed(previous.action, view.action)
            ):
                continue
            revision = DecisionRevision(
                decision_key=previous.decision_key,
                revision=previous.revision + 1,
                supersedes_id=previous.id,
                dossier_id=previous.dossier_id,
                instrument_id=instrument.id,
                horizon=view.horizon,
                action=view.action,
                target_weight=view.target_weight,
                expected_return_low=view.expected_return_low,
                expected_return_high=view.expected_return_high,
                probability_up=view.probability_up,
                confidence=view.confidence,
                holding_days=view.holding_days,
                rationale=view.rationale,
                risks=view.risks,
                trigger_reason=trigger_reason,
                evidence_ids=[],
                strategy_version_id=previous.strategy_version_id,
                risk_version=self.risk.version,
            )
            self.session.add(revision)
            revisions.append(revision)
        self.session.flush()
        return revisions

    def _discover_opportunities(self, trade_date: date, limit: int) -> list[Instrument]:
        instruments = list(
            self.session.scalars(
                select(Instrument)
                .where(Instrument.investable.is_(True))
                .order_by(Instrument.median_turnover_20d.desc())
            )
        )
        evidence_counts = dict(
            self.session.execute(
                select(EvidenceRef.instrument_id, func.count(EvidenceRef.id))
                .where(
                    EvidenceRef.published_at
                    >= datetime.combine(trade_date - timedelta(days=30), datetime.min.time(), SHANGHAI)
                )
                .group_by(EvidenceRef.instrument_id)
            ).all()
        )
        scored: list[tuple[Decimal, Instrument]] = []
        for instrument in instruments:
            history = self.market.history(instrument, end=trade_date, limit=20)
            if len(history) < 6:
                continue
            latest = Decimal(str(history[-1]["close"]))
            prior = Decimal(str(history[-6]["close"]))
            move = abs(latest / prior - 1) if prior else Decimal(0)
            event_score = Decimal(evidence_counts.get(instrument.id, 0))
            liquidity = min(Decimal(instrument.median_turnover_20d or 0) / Decimal("1000000000"), Decimal(2))
            scored.append((event_score * 3 + move * 10 + liquidity, instrument))
        scored.sort(key=lambda item: item[0], reverse=True)
        held_ids = set(
            self.session.scalars(select(PositionLot.instrument_id).where(PositionLot.remaining_quantity > 0))
        )
        held = [instrument for _, instrument in scored if instrument.id in held_ids]
        others = [instrument for _, instrument in scored if instrument.id not in held_ids]
        return (held + others)[:limit]

    def _market_regime(self, trade_date: date) -> str:
        benchmark = self.market.benchmark_instrument()
        if benchmark is None:
            return "UNKNOWN"
        history = self.market.history(benchmark, end=trade_date, limit=20)
        if len(history) < 20:
            return "UNKNOWN"
        first = Decimal(str(history[0]["close"]))
        latest = Decimal(str(history[-1]["close"]))
        change = latest / first - 1
        if change >= Decimal("0.05"):
            return "BULL"
        if change <= Decimal("-0.05"):
            return "BEAR"
        return "NEUTRAL"

    def _research_instrument(
        self,
        run: AgentRun,
        instrument: Instrument,
        trade_date: date,
        market_regime: str,
        champion: StrategyVersion,
        *,
        reporter: RunProgressReporter | None = None,
        index: int = 1,
        total: int = 1,
    ) -> ResearchBundle:
        evidence = list(
            self.session.scalars(
                select(EvidenceRef)
                .where(
                    (EvidenceRef.instrument_id == instrument.id) | EvidenceRef.instrument_id.is_(None),
                    EvidenceRef.published_at <= utc_now(),
                )
                .order_by(EvidenceRef.published_at.desc())
                .limit(12)
            )
        )
        history = self.market.history(instrument, end=trade_date, limit=60)
        experiences = list(
            self.session.scalars(
                select(Experience)
                .where(Experience.instrument_id == instrument.id)
                .order_by(Experience.created_at.desc())
                .limit(8)
            )
        )
        context = {
            "instrument": {
                "id": instrument.id,
                "symbol": instrument.symbol,
                "name": instrument.name,
                "asset_type": instrument.asset_type,
                "industry": instrument.industry,
            },
            "trade_date": trade_date,
            "market_regime": market_regime,
            "price_history": history,
            "evidence": [
                {
                    "id": item.id,
                    "source": item.source_id,
                    "title": item.title,
                    "excerpt": item.excerpt,
                    "published_at": item.published_at,
                    "credibility": item.credibility,
                }
                for item in evidence
            ],
            "similar_experiences": [
                {
                    "horizon": item.horizon,
                    "thesis": item.thesis_summary,
                    "excess_return": str(item.excess_return),
                    "attribution": item.attribution,
                }
                for item in experiences
            ],
            "champion_rules": champion.rules,
        }
        base_progress = (index - 1) * 3
        if reporter:
            reporter.update(
                "RESEARCH_THESIS",
                f"{instrument.symbol} {instrument.name}：生成正方研究",
                current=base_progress,
                total=total * 3 + 1,
            )
        thesis = self.model.complete_json(
            purpose="research-thesis",
            system=SYSTEM_PROMPT,
            user=json.dumps(context, ensure_ascii=False, default=str),
            schema=ThesisOutput,
            run_id=run.id,
        )
        if reporter:
            reporter.update(
                "RESEARCH_OPPOSITION",
                f"{instrument.symbol} {instrument.name}：寻找最强反证",
                current=base_progress + 1,
                total=total * 3 + 1,
            )
        opposition = self.model.complete_json(
            purpose="research-opposition",
            system=SYSTEM_PROMPT + "\n此阶段只寻找最强反证，不得迎合正方。",
            user=json.dumps(
                {"context": context, "thesis": thesis.model_dump(mode="json")},
                ensure_ascii=False,
                default=str,
            ),
            schema=OppositionOutput,
            run_id=run.id,
        )
        if reporter:
            reporter.update(
                "RESEARCH_SYNTHESIS",
                f"{instrument.symbol} {instrument.name}：综合三个周期结论",
                current=base_progress + 2,
                total=total * 3 + 1,
            )
        synthesis = self.model.complete_json(
            purpose="research-synthesis",
            system=SYSTEM_PROMPT + "\n综合正反两方；证据不足时必须 WATCH 或 REJECT。",
            user=json.dumps(
                {
                    "context": context,
                    "thesis": thesis.model_dump(mode="json"),
                    "opposition": opposition.model_dump(mode="json"),
                },
                ensure_ascii=False,
                default=str,
            ),
            schema=SynthesisOutput,
            run_id=run.id,
        )
        return ResearchBundle(
            instrument_id=instrument.id,
            symbol=instrument.symbol,
            trade_date=trade_date,
            thesis=thesis,
            opposition=opposition,
            synthesis=synthesis,
            evidence_ids=[item.id for item in evidence],
        )

    def _portfolio_proposal(
        self,
        run: AgentRun,
        research: list[ResearchBundle],
        market_regime: str,
    ) -> PortfolioProposalInput:
        account = self.session.scalar(select(Account).where(Account.name == "paper-main"))
        positions = list(
            self.session.execute(
                select(PositionLot.instrument_id, PositionLot.horizon, func.sum(PositionLot.remaining_quantity))
                .where(PositionLot.remaining_quantity > 0)
                .group_by(PositionLot.instrument_id, PositionLot.horizon)
            )
        )
        payload = {
            "market_regime": market_regime,
            "account_cash": str(account.cash if account else 0),
            "current_positions": [
                {"instrument_id": row[0], "horizon": row[1], "quantity": row[2]} for row in positions
            ],
            "research": [item.model_dump(mode="json") for item in research],
            "constraints": {
                "instrument_max": 0.10,
                "industry_max": 0.25,
                "gross_max": 0.85,
                "cash_min": 0.15,
                "position_count_max": 10,
                "horizon_bounds": {
                    "SHORT": [0.10, 0.30],
                    "SWING": [0.30, 0.50],
                    "LONG": [0.30, 0.50],
                },
                "cash_is_valid": True,
            },
        }
        return self.model.complete_json(
            purpose="portfolio-allocation",
            system=SYSTEM_PROMPT + "\n你只提出目标组合；硬风控将在模型外独立执行。",
            user=json.dumps(payload, ensure_ascii=False, default=str),
            schema=PortfolioProposalInput,
            run_id=run.id,
        )

    def _persist_research(
        self,
        run: AgentRun,
        research: list[ResearchBundle],
        champion: StrategyVersion,
    ) -> dict[tuple[str, Horizon], DecisionRevision]:
        decisions: dict[tuple[str, Horizon], DecisionRevision] = {}
        for item in research:
            dossier = ResearchDossier(
                run_id=run.id,
                instrument_id=item.instrument_id,
                trade_date=item.trade_date,
                thesis=item.thesis.model_dump(mode="json"),
                opposition=item.opposition.model_dump(mode="json"),
                synthesis=item.synthesis.model_dump(mode="json"),
                evidence_ids=item.evidence_ids,
                strategy_version_id=champion.id,
                model_version=self.model.model_name,
                prompt_version=self.prompt_version,
                data_versions={
                    "as_of": item.trade_date.isoformat(),
                    "artifact_ids": [
                        artifact.id
                        for artifact in self.market.confirmed_artifacts(
                            self.session.get(Instrument, item.instrument_id)
                        )
                    ],
                },
            )
            self.session.add(dossier)
            self.session.flush()
            for view in item.synthesis.horizon_views:
                decision = DecisionRevision(
                    decision_key=f"{item.instrument_id}:{item.trade_date}:{view.horizon}",
                    revision=1,
                    dossier_id=dossier.id,
                    instrument_id=item.instrument_id,
                    horizon=view.horizon,
                    action=view.action,
                    target_weight=view.target_weight,
                    expected_return_low=view.expected_return_low,
                    expected_return_high=view.expected_return_high,
                    probability_up=view.probability_up,
                    confidence=view.confidence,
                    holding_days=view.holding_days,
                    rationale=view.rationale,
                    risks=view.risks,
                    trigger_reason="EOD_RESEARCH",
                    evidence_ids=item.evidence_ids,
                    strategy_version_id=champion.id,
                    risk_version=self.risk.version,
                )
                self.session.add(decision)
                decisions[(item.instrument_id, view.horizon)] = decision
        self.session.flush()
        return decisions

    def _block(self, run: AgentRun, reason: str) -> AgentRun:
        run.status = RunStatus.BLOCKED
        run.blocker = reason
        run.finished_at = utc_now()
        run.updated_at = run.finished_at
        run.stage = "BLOCKED"
        run.progress_message = reason
        run.result = {"cash_is_valid": True}
        self.session.commit()
        return run

    @staticmethod
    def _direction_reversed(previous: str, current: DecisionAction) -> bool:
        positive = {DecisionAction.BUY, DecisionAction.HOLD}
        negative = {DecisionAction.REDUCE, DecisionAction.SELL}
        return (previous in positive and current in negative) or (previous in negative and current in positive)
