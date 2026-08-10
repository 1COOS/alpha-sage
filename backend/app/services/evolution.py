from __future__ import annotations

import json
import math
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import (
    ChallengerStatus,
    DecisionAction,
    RunKind,
    RunStatus,
    StrategyStatus,
)
from app.domain.schemas import (
    ChallengerCandidateOutput,
    ReplayPredictionsOutput,
    SynthesisOutput,
    WeeklyLessonsOutput,
)
from app.models import (
    AgentRun,
    ChallengerReport,
    DecisionRevision,
    Experience,
    Instrument,
    LessonCandidate,
    OrderPlan,
    PaperFill,
    ResearchDossier,
    StrategyVersion,
    UserFeedback,
    utc_now,
)
from app.services.agent import SYSTEM_PROMPT
from app.services.audit import append_audit, stable_hash
from app.services.market_repository import MarketRepository
from app.services.model import OpenAICompatibleModel, StructuredModel
from app.services.run_queue import RunProgressReporter
from app.temporal import beijing_today


class ExperienceService:
    def __init__(self, session: Session):
        self.session = session
        self.market = MarketRepository(session)

    def attribute_due(self, as_of: date | None = None) -> list[Experience]:
        as_of = as_of or beijing_today()
        decisions = list(
            self.session.scalars(
                select(DecisionRevision)
                .outerjoin(Experience, Experience.decision_id == DecisionRevision.id)
                .where(Experience.id.is_(None))
                .order_by(DecisionRevision.created_at)
            )
        )
        benchmark = self.market.benchmark_instrument()
        if benchmark is None:
            return []
        created: list[Experience] = []
        for decision in decisions:
            instrument = self.session.get(Instrument, decision.instrument_id)
            if instrument is None:
                continue
            start_date = decision.created_at.date()
            history = self.market.history(instrument, start=start_date, end=as_of)
            if len(history) <= decision.holding_days:
                continue
            entry_row = history[0]
            exit_row = history[decision.holding_days]
            entry_date = date.fromisoformat(str(entry_row["trade_date"])[:10])
            exit_date = date.fromisoformat(str(exit_row["trade_date"])[:10])
            fill = self.session.scalar(
                select(PaperFill)
                .join(OrderPlan, PaperFill.order_id == OrderPlan.id)
                .where(OrderPlan.decision_id == decision.id)
            )
            entry_price = Decimal(str(fill.fill_price)) if fill else Decimal(str(entry_row["open"])) * Decimal("1.001")
            exit_price = Decimal(str(exit_row["close"]))
            raw_return = exit_price / entry_price - 1
            positive_action = decision.action in {DecisionAction.BUY, DecisionAction.HOLD}
            net_return = raw_return if positive_action else -raw_return
            net_return -= Decimal("0.0016")
            benchmark_rows = self.market.history(benchmark, start=entry_date, end=exit_date)
            benchmark_return = Decimal(0)
            if len(benchmark_rows) >= 2:
                benchmark_return = (
                    Decimal(str(benchmark_rows[-1]["close"])) / Decimal(str(benchmark_rows[0]["open"])) - 1
                )
            excess = net_return - benchmark_return
            probability = Decimal(decision.probability_up)
            outcome = Decimal(1) if raw_return > 0 else Decimal(0)
            brier = (probability - outcome) ** 2
            path = [Decimal(str(row["close"])) / entry_price - 1 for row in history[: decision.holding_days + 1]]
            if not positive_action:
                path = [-item for item in path]
            dossier = self.session.get(ResearchDossier, decision.dossier_id)
            experience = Experience(
                decision_id=decision.id,
                strategy_version_id=decision.strategy_version_id,
                instrument_id=instrument.id,
                horizon=decision.horizon,
                market_regime=(dossier.synthesis if dossier else {}).get("market_regime", "UNKNOWN"),
                event_types=[],
                thesis_summary=decision.rationale,
                outcome_date=exit_date,
                net_return=net_return,
                benchmark_return=benchmark_return,
                excess_return=excess,
                direction_hit=(net_return > 0),
                brier_score=brier,
                max_favorable_excursion=max(path, default=Decimal(0)),
                max_adverse_excursion=min(path, default=Decimal(0)),
                attribution={
                    "entry_date": entry_date.isoformat(),
                    "exit_date": exit_date.isoformat(),
                    "entry_price": str(entry_price),
                    "exit_price": str(exit_price),
                    "fees_and_slippage_estimate": "0.0016",
                },
                tags=[decision.horizon, decision.action],
            )
            self.session.add(experience)
            created.append(experience)
        self.session.flush()
        self._update_shadow_reports()
        self.session.commit()
        return created

    def _update_shadow_reports(self) -> None:
        reports = list(
            self.session.scalars(select(ChallengerReport).where(ChallengerReport.status == ChallengerStatus.SHADOW))
        )
        for report in reports:
            rows = list(
                self.session.scalars(
                    select(Experience)
                    .where(Experience.strategy_version_id == report.strategy_version_id)
                    .order_by(Experience.outcome_date)
                )
            )
            if not rows:
                continue
            report.shadow_days = len({row.outcome_date for row in rows})
            report.net_excess_return = sum((row.excess_return for row in rows), Decimal(0))
            report.calibration_score = sum((row.brier_score for row in rows), Decimal(0)) / len(rows)
            report.max_drawdown = self._max_drawdown([row.net_return for row in rows])
            if (
                report.shadow_days >= 20
                and report.net_excess_return > 0
                and report.net_excess_return > report.champion_excess_return
                and report.max_drawdown <= report.champion_max_drawdown
                and report.calibration_score <= report.champion_calibration_score
                and report.hard_risk_violations == 0
            ):
                report.status = ChallengerStatus.ELIGIBLE

    @staticmethod
    def _max_drawdown(returns: list[Decimal]) -> Decimal:
        equity = Decimal(1)
        high = Decimal(1)
        maximum = Decimal(0)
        for value in returns:
            equity *= Decimal(1) + value
            high = max(high, equity)
            if high > 0:
                maximum = max(maximum, (high - equity) / high)
        return maximum


class EvolutionService:
    def __init__(self, session: Session, model: StructuredModel | None = None):
        self.session = session
        self._model = model

    @property
    def model(self) -> StructuredModel:
        if self._model is None:
            self._model = OpenAICompatibleModel(self.session)
        return self._model

    def generate_weekly_lessons(
        self,
        week_ending: date | None = None,
        *,
        run: AgentRun | None = None,
        reporter: RunProgressReporter | None = None,
    ) -> AgentRun:
        week_ending = week_ending or beijing_today()
        if run is None:
            run = AgentRun(kind=RunKind.WEEKLY, status=RunStatus.RUNNING, trade_date=week_ending)
            self.session.add(run)
            self.session.commit()
        run.trade_date = week_ending
        if reporter:
            reporter.update("COLLECT_EXPERIENCES", "收集最近两周已归因经验")
        experiences = list(
            self.session.scalars(
                select(Experience)
                .where(Experience.outcome_date >= week_ending - timedelta(days=14))
                .order_by(Experience.outcome_date)
            )
        )
        if not experiences:
            return self._block(run, "没有可供周度总结的已归因经验")
        if reporter:
            reporter.update("GENERATE_LESSONS", f"基于 {len(experiences)} 条经验生成周度规律")
        output = self.model.complete_json(
            purpose="weekly-lessons",
            system=SYSTEM_PROMPT + "\n只提出可证伪的经验假设，不得将相关性写成确定因果。",
            user=json.dumps(
                [
                    {
                        "id": item.id,
                        "horizon": item.horizon,
                        "regime": item.market_regime,
                        "thesis": item.thesis_summary,
                        "excess_return": str(item.excess_return),
                        "brier_score": str(item.brier_score),
                        "attribution": item.attribution,
                    }
                    for item in experiences
                ],
                ensure_ascii=False,
                default=str,
            ),
            schema=WeeklyLessonsOutput,
            run_id=run.id,
        )
        for lesson in output.lessons:
            self.session.add(
                LessonCandidate(
                    week_ending=week_ending,
                    scope=lesson.scope,
                    hypothesis=lesson.hypothesis,
                    supporting_experience_ids=lesson.supporting_experience_ids,
                    contradicting_experience_ids=lesson.contradicting_experience_ids,
                    confidence=lesson.confidence,
                )
            )
        run.status = RunStatus.COMPLETED
        run.finished_at = utc_now()
        run.updated_at = run.finished_at
        run.stage = "COMPLETED"
        run.progress_message = "周度规律生成完成"
        run.result = {"lesson_count": len(output.lessons)}
        self.session.commit()
        return run

    def generate_monthly_challenger(
        self,
        as_of: date | None = None,
        *,
        run: AgentRun | None = None,
        reporter: RunProgressReporter | None = None,
    ) -> AgentRun:
        as_of = as_of or beijing_today()
        if run is None:
            run = AgentRun(kind=RunKind.MONTHLY, status=RunStatus.RUNNING, trade_date=as_of)
            self.session.add(run)
            self.session.commit()
        run.trade_date = as_of
        if reporter:
            reporter.update("COLLECT_EVIDENCE", "收集经验、规律和独立用户反馈")
        champion = self.session.scalar(select(StrategyVersion).where(StrategyVersion.status == StrategyStatus.CHAMPION))
        experiences = list(self.session.scalars(select(Experience).order_by(Experience.outcome_date.desc()).limit(250)))
        experiences.reverse()
        training, holdout = self._split_replay_cases(experiences)
        holdout_ids = {item.id for item in holdout}
        lessons = list(
            self.session.scalars(
                select(LessonCandidate).where(LessonCandidate.status == "PROPOSED").order_by(LessonCandidate.created_at)
            )
        )
        lessons = [
            item
            for item in lessons
            if not holdout_ids.intersection(item.supporting_experience_ids + item.contradicting_experience_ids)
        ]
        feedback = list(
            self.session.scalars(
                select(UserFeedback)
                .where(UserFeedback.used_for_challenger.is_(False))
                .order_by(UserFeedback.created_at)
            )
        )
        if champion is None or len(experiences) < 50 or not lessons:
            return self._block(run, "挑战者至少需要50条已归因经验和一组周度规律候选")
        if reporter:
            reporter.update("GENERATE_CHALLENGER", "生成仅包含规则、权重和提示词变化的挑战者")
        candidate = self.model.complete_json(
            purpose="monthly-challenger",
            system=SYSTEM_PROMPT + "\n只能修改经验规则、证据权重和提示词，不得生成或修改代码。",
            user=json.dumps(
                {
                    "champion": champion.rules,
                    "training_cases": [
                        {
                            "id": item.id,
                            "horizon": item.horizon,
                            "market_regime": item.market_regime,
                            "event_types": item.event_types,
                            "thesis": item.thesis_summary,
                            "excess_return": str(item.excess_return),
                            "brier_score": str(item.brier_score),
                        }
                        for item in training
                    ],
                    "lessons": [
                        {
                            "id": item.id,
                            "scope": item.scope,
                            "hypothesis": item.hypothesis,
                            "confidence": str(item.confidence),
                        }
                        for item in lessons
                    ],
                    "user_feedback": [
                        {
                            "id": item.id,
                            "target_type": item.target_type,
                            "content": item.content,
                            "sentiment": item.sentiment,
                        }
                        for item in feedback
                    ],
                },
                ensure_ascii=False,
                default=str,
            ),
            schema=ChallengerCandidateOutput,
            run_id=run.id,
        )
        rules = champion.rules | candidate.rule_changes
        if reporter:
            reporter.update("REPLAY", f"在 {len(holdout)} 个冻结留出案例上执行回放")
        strategy = StrategyVersion(
            version=f"challenger-{as_of.isoformat()}-{stable_hash(rules)[:8]}",
            status=StrategyStatus.CHALLENGER,
            parent_id=champion.id,
            rules=rules,
            prompt_overrides=candidate.prompt_overrides,
            evidence_weights={key: str(value) for key, value in candidate.evidence_weights.items()},
            content_hash=stable_hash(
                {"rules": rules, "prompts": candidate.prompt_overrides, "weights": candidate.evidence_weights}
            ),
        )
        self.session.add(strategy)
        self.session.flush()
        replay = self._evaluate_replay(run, strategy, champion, holdout)
        report = ChallengerReport(
            strategy_version_id=strategy.id,
            champion_version_id=champion.id,
            status=ChallengerStatus.SHADOW if replay["passed"] else ChallengerStatus.BLOCKED_INSUFFICIENT_EVIDENCE,
            replay_case_count=len(holdout),
            net_excess_return=replay["candidate_excess"],
            champion_excess_return=replay["champion_excess"],
            max_drawdown=replay["candidate_drawdown"],
            champion_max_drawdown=replay["champion_drawdown"],
            calibration_score=replay["candidate_brier"],
            champion_calibration_score=replay["champion_brier"],
            metrics={"rationale": candidate.rationale, "replay": {key: str(value) for key, value in replay.items()}},
        )
        self.session.add(report)
        for item in feedback:
            item.used_for_challenger = True
        run.status = RunStatus.COMPLETED
        run.finished_at = utc_now()
        run.updated_at = run.finished_at
        run.stage = "COMPLETED"
        run.progress_message = "月度挑战者生成完成"
        run.result = {"challenger_report_id": report.id, "status": report.status}
        self.session.commit()
        return run

    def create_shadow_decisions(self, run: AgentRun) -> int:
        reports = list(
            self.session.scalars(select(ChallengerReport).where(ChallengerReport.status == ChallengerStatus.SHADOW))
        )
        dossiers = list(self.session.scalars(select(ResearchDossier).where(ResearchDossier.run_id == run.id)))
        count = 0
        for report in reports:
            strategy = self.session.get(StrategyVersion, report.strategy_version_id)
            if strategy is None:
                continue
            for dossier in dossiers:
                synthesis = self.model.complete_json(
                    purpose="challenger-shadow-decision",
                    system=SYSTEM_PROMPT + "\n这是挑战者影子决策，不得创建真实或模拟订单。",
                    user=json.dumps(
                        {
                            "thesis": dossier.thesis,
                            "opposition": dossier.opposition,
                            "candidate_rules": strategy.rules,
                            "prompt_overrides": strategy.prompt_overrides,
                        },
                        ensure_ascii=False,
                    ),
                    schema=SynthesisOutput,
                    run_id=run.id,
                )
                for view in synthesis.horizon_views:
                    self.session.add(
                        DecisionRevision(
                            decision_key=f"shadow:{strategy.id}:{dossier.instrument_id}:{dossier.trade_date}:{view.horizon}",
                            revision=1,
                            dossier_id=dossier.id,
                            instrument_id=dossier.instrument_id,
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
                            trigger_reason="CHALLENGER_SHADOW",
                            evidence_ids=dossier.evidence_ids,
                            strategy_version_id=strategy.id,
                            risk_version="risk-v1",
                        )
                    )
                    count += 1
        self.session.flush()
        return count

    def approve(self, report_id: str, reason: str) -> ChallengerReport:
        report = self.session.get(ChallengerReport, report_id)
        if report is None:
            raise ValueError("挑战者报告不存在")
        if report.status != ChallengerStatus.ELIGIBLE:
            raise ValueError("挑战者尚未通过历史回放和20个交易日影子门槛")
        champion = self.session.get(StrategyVersion, report.champion_version_id)
        challenger = self.session.get(StrategyVersion, report.strategy_version_id)
        if champion is None or challenger is None or champion.status != StrategyStatus.CHAMPION:
            raise ValueError("冠军版本链已经变化，请重新评估")
        champion.status = StrategyStatus.SUPERSEDED
        challenger.status = StrategyStatus.CHAMPION
        challenger.activated_at = utc_now()
        report.status = ChallengerStatus.APPROVED
        report.approved_at = utc_now()
        report.approved_reason = reason
        append_audit(
            self.session,
            event_type="CHALLENGER_APPROVED",
            actor="user",
            entity_type="ChallengerReport",
            entity_id=report.id,
            payload={"reason": reason, "new_champion": challenger.version},
        )
        self.session.commit()
        return report

    def rollback(self, reason: str) -> StrategyVersion:
        champion = self.session.scalar(select(StrategyVersion).where(StrategyVersion.status == StrategyStatus.CHAMPION))
        if champion is None or champion.parent_id is None:
            raise ValueError("没有可回滚的上一冠军")
        parent = self.session.get(StrategyVersion, champion.parent_id)
        if parent is None:
            raise ValueError("上一冠军版本缺失")
        champion.status = StrategyStatus.SUPERSEDED
        parent.status = StrategyStatus.CHAMPION
        parent.activated_at = utc_now()
        append_audit(
            self.session,
            event_type="STRATEGY_ROLLBACK",
            actor="user",
            entity_type="StrategyVersion",
            entity_id=parent.id,
            payload={"reason": reason, "from": champion.version, "to": parent.version},
        )
        self.session.commit()
        return parent

    def _evaluate_replay(
        self,
        run: AgentRun,
        candidate: StrategyVersion,
        champion: StrategyVersion,
        holdout: list[Experience],
    ) -> dict[str, Decimal | bool]:
        candidate_predictions = self._replay_predictions(
            run=run,
            strategy=candidate,
            holdout=holdout,
            purpose="challenger-sealed-replay",
        )
        champion_predictions = self._replay_predictions(
            run=run,
            strategy=champion,
            holdout=holdout,
            purpose="champion-sealed-replay",
        )
        candidate_by_id = {item.experience_id: item for item in candidate_predictions.predictions}
        champion_by_id = {item.experience_id: item for item in champion_predictions.predictions}
        candidate_returns, candidate_briers = self._score_predictions(holdout, candidate_by_id)
        champion_returns, champion_briers = self._score_predictions(holdout, champion_by_id)
        candidate_excess = sum(candidate_returns, Decimal(0))
        champion_excess = sum(champion_returns, Decimal(0))
        candidate_drawdown = ExperienceService._max_drawdown(candidate_returns)
        champion_drawdown = ExperienceService._max_drawdown(champion_returns)
        candidate_brier = sum(candidate_briers, Decimal(0)) / len(candidate_briers)
        champion_brier = sum(champion_briers, Decimal(0)) / len(champion_briers)
        passed = (
            candidate_excess > 0
            and candidate_excess > champion_excess
            and candidate_drawdown <= champion_drawdown
            and candidate_brier <= champion_brier
        )
        return {
            "passed": passed,
            "candidate_excess": candidate_excess,
            "champion_excess": champion_excess,
            "candidate_drawdown": candidate_drawdown,
            "champion_drawdown": champion_drawdown,
            "candidate_brier": candidate_brier,
            "champion_brier": champion_brier,
        }

    def _replay_predictions(
        self,
        *,
        run: AgentRun,
        strategy: StrategyVersion,
        holdout: list[Experience],
        purpose: str,
    ) -> ReplayPredictionsOutput:
        return self.model.complete_json(
            purpose=purpose,
            system=SYSTEM_PROMPT + "\n以下案例不含结果。按挑战者规则给出当时会采取的判断。",
            user=json.dumps(
                {
                    "strategy_rules": strategy.rules,
                    "cases": [
                        {
                            "experience_id": item.id,
                            "horizon": item.horizon,
                            "market_regime": item.market_regime,
                            "event_types": item.event_types,
                            "thesis": item.thesis_summary,
                        }
                        for item in holdout
                    ],
                },
                ensure_ascii=False,
            ),
            schema=ReplayPredictionsOutput,
            run_id=run.id,
        )

    @staticmethod
    def _score_predictions(holdout: list[Experience], by_id: dict) -> tuple[list[Decimal], list[Decimal]]:
        returns: list[Decimal] = []
        briers: list[Decimal] = []
        for item in holdout:
            prediction = by_id.get(item.id)
            if prediction is None:
                returns.append(Decimal(0))
                briers.append(Decimal(1))
                continue
            invested = prediction.action in {DecisionAction.BUY, DecisionAction.HOLD}
            returns.append(item.excess_return if invested else Decimal(0))
            outcome = Decimal(1) if item.net_return > 0 else Decimal(0)
            briers.append((prediction.probability_up - outcome) ** 2)
        return returns, briers

    @staticmethod
    def _split_replay_cases(experiences: list[Experience]) -> tuple[list[Experience], list[Experience]]:
        if len(experiences) < 50:
            return experiences, []
        holdout_size = max(20, math.ceil(len(experiences) * 0.2))
        return experiences[:-holdout_size], experiences[-holdout_size:]

    def _block(self, run: AgentRun, reason: str) -> AgentRun:
        run.status = RunStatus.BLOCKED
        run.blocker = reason
        run.finished_at = utc_now()
        run.updated_at = run.finished_at
        run.stage = "BLOCKED"
        run.progress_message = reason
        self.session.commit()
        return run
