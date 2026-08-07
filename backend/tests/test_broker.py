from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.domain.enums import OrderStatus
from app.models import (
    Account,
    AgentRun,
    DataArtifact,
    DecisionRevision,
    Instrument,
    OrderPlan,
    PositionLot,
    ResearchDossier,
    StrategyVersion,
)
from app.services.broker import FillBlocked, PaperBroker
from app.services.providers import Bar, SpotQuote

SHANGHAI = ZoneInfo("Asia/Shanghai")


def setup_order(session, side: str, trade_date: date):
    account = session.scalar(select(Account).where(Account.name == "paper-main"))
    account.enabled = True
    strategy = session.scalar(select(StrategyVersion).limit(1))
    instrument = Instrument(
        exchange="SSE",
        symbol="600000",
        name="浦发银行",
        asset_type="STOCK",
        investable=True,
        tick_size=Decimal("0.01"),
    )
    session.add(instrument)
    session.flush()
    run = AgentRun(kind="EOD", status="RUNNING", trade_date=trade_date)
    session.add(run)
    session.flush()
    dossier = ResearchDossier(
        run_id=run.id,
        instrument_id=instrument.id,
        trade_date=trade_date,
        thesis={},
        opposition={},
        synthesis={},
        evidence_ids=[],
        strategy_version_id=strategy.id,
        model_version="test",
        prompt_version="test",
        data_versions={},
    )
    session.add(dossier)
    session.flush()
    decision = DecisionRevision(
        decision_key=f"test:{side}",
        revision=1,
        dossier_id=dossier.id,
        instrument_id=instrument.id,
        horizon="SHORT",
        action="BUY" if side == "BUY" else "SELL",
        target_weight=Decimal("0.05"),
        expected_return_low=Decimal("-0.02"),
        expected_return_high=Decimal("0.05"),
        probability_up=Decimal("0.6"),
        confidence=Decimal("0.6"),
        holding_days=3,
        rationale="test decision",
        risks=[],
        trigger_reason="TEST",
        evidence_ids=[],
        strategy_version_id=strategy.id,
        risk_version="risk-v1",
    )
    session.add(decision)
    session.flush()
    order = OrderPlan(
        account_id=account.id,
        decision_id=decision.id,
        instrument_id=instrument.id,
        horizon="SHORT",
        side=side,
        quantity=100,
        status=OrderStatus.PENDING,
        eligible_after=datetime.combine(trade_date, datetime.min.time(), SHANGHAI),
    )
    artifact = DataArtifact(
        dataset="intraday_bar_confirmed",
        provider="dual",
        path="/tmp/test.parquet",
        sha256="a" * 64,
        rows=1,
        available_at=datetime.combine(trade_date, datetime.min.time(), SHANGHAI),
    )
    session.add_all([order, artifact])
    session.commit()
    return account, instrument, order, artifact


def market_data(instrument: Instrument, trade_date: date):
    observed = datetime.combine(trade_date, datetime.min.time(), SHANGHAI).replace(hour=10)
    bar = Bar(
        symbol=instrument.symbol,
        exchange=instrument.exchange,
        trade_date=trade_date,
        observed_at=observed,
        available_at=observed,
        open=Decimal("10"),
        high=Decimal("10.10"),
        low=Decimal("9.90"),
        close=Decimal("10"),
        previous_close=Decimal("9.95"),
        volume=Decimal("100000"),
        turnover=Decimal("1000000"),
        provider="eastmoney",
        interval_minutes=5,
    )
    quote = SpotQuote(
        symbol=instrument.symbol,
        exchange=instrument.exchange,
        observed_at=observed,
        price=Decimal("10"),
        open=Decimal("10"),
        high=Decimal("10.10"),
        low=Decimal("9.90"),
        previous_close=Decimal("9.95"),
        volume=Decimal("100000"),
        turnover=Decimal("1000000"),
        provider="tencent",
    )
    return bar, quote


def test_paper_broker_applies_slippage_and_creates_lot(session):
    account, instrument, order, artifact = setup_order(session, "BUY", date(2026, 8, 5))
    bar, quote = market_data(instrument, date(2026, 8, 5))
    fill = PaperBroker(session).execute(
        order=order,
        bar=bar,
        confirmation=quote,
        artifact_id=artifact.id,
    )
    lot = session.scalar(select(PositionLot))
    assert fill.fill_price == Decimal("10.0100")
    assert fill.commission == Decimal("5.0000")
    assert fill.currency == "CNY"
    assert fill.local_trade_date == date(2026, 8, 5)
    assert fill.market_rule_version_id is not None
    assert lot is not None and lot.remaining_quantity == 100
    assert account.cash < Decimal("999000")


def test_t_plus_one_blocks_same_day_sell(session):
    account, instrument, buy_order, artifact = setup_order(session, "BUY", date(2026, 8, 5))
    bar, quote = market_data(instrument, date(2026, 8, 5))
    PaperBroker(session).execute(
        order=buy_order,
        bar=bar,
        confirmation=quote,
        artifact_id=artifact.id,
    )
    sell_decision = session.get(DecisionRevision, buy_order.decision_id)
    sell_order = OrderPlan(
        account_id=account.id,
        decision_id=sell_decision.id,
        instrument_id=instrument.id,
        horizon="SHORT",
        side="SELL",
        quantity=100,
        status=OrderStatus.PENDING,
        eligible_after=bar.observed_at,
    )
    session.add(sell_order)
    session.commit()
    with pytest.raises(FillBlocked, match=r"T\+1"):
        PaperBroker(session).execute(
            order=sell_order,
            bar=bar,
            confirmation=quote,
            artifact_id=artifact.id,
        )
