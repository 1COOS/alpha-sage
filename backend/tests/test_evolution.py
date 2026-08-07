from decimal import Decimal

import pytest
from sqlalchemy import select

from app.domain.enums import ChallengerStatus, StrategyStatus
from app.models import ChallengerReport, StrategyVersion
from app.services.audit import stable_hash
from app.services.evolution import EvolutionService
from app.services.model import FunctionModel


def test_challenger_requires_eligible_status_for_approval(session):
    champion = session.scalar(select(StrategyVersion).where(StrategyVersion.status == StrategyStatus.CHAMPION))
    challenger = StrategyVersion(
        version="challenger-test",
        status=StrategyStatus.CHALLENGER,
        parent_id=champion.id,
        rules={"test": True},
        content_hash=stable_hash({"test": True}),
    )
    session.add(challenger)
    session.flush()
    report = ChallengerReport(
        strategy_version_id=challenger.id,
        champion_version_id=champion.id,
        status=ChallengerStatus.SHADOW,
        replay_case_count=50,
        shadow_days=10,
        net_excess_return=Decimal("0.01"),
        champion_excess_return=Decimal("0"),
        max_drawdown=Decimal("0.02"),
        champion_max_drawdown=Decimal("0.03"),
        calibration_score=Decimal("0.15"),
        champion_calibration_score=Decimal("0.2"),
    )
    session.add(report)
    session.commit()
    with pytest.raises(ValueError, match="20个交易日"):
        EvolutionService(session, FunctionModel(lambda _purpose, _schema: {})).approve(
            report.id, "not enough shadow days"
        )


def test_replay_split_freezes_latest_twenty_percent_as_holdout():
    cases = list(range(100))
    training, holdout = EvolutionService._split_replay_cases(cases)  # type: ignore[arg-type]
    assert training == list(range(80))
    assert holdout == list(range(80, 100))
