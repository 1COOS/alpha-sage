from decimal import Decimal

from sqlalchemy import select

from app.domain.enums import Horizon
from app.domain.schemas import PortfolioAllocation, PortfolioProposalInput
from app.models import Account, Instrument
from app.services.risk import RiskEngine


def add_instrument(session, symbol: str, industry: str = "银行") -> Instrument:
    instrument = Instrument(
        exchange="SSE",
        symbol=symbol,
        name=f"标的{symbol}",
        asset_type="STOCK",
        industry=industry,
        investable=True,
        tick_size=Decimal("0.01"),
    )
    session.add(instrument)
    session.flush()
    return instrument


def test_risk_rejects_combined_symbol_weight_above_ten_percent(session):
    instrument = add_instrument(session, "600000")
    proposal = PortfolioProposalInput(
        allocations=[
            PortfolioAllocation(
                instrument_id=instrument.id,
                horizon=Horizon.SHORT,
                target_weight=Decimal("0.06"),
                reason="short",
            ),
            PortfolioAllocation(
                instrument_id=instrument.id,
                horizon=Horizon.LONG,
                target_weight=Decimal("0.06"),
                reason="long",
            ),
        ],
        cash_weight=Decimal("0.88"),
        market_regime="NEUTRAL",
        rationale="test",
    )
    result = RiskEngine(session).validate_proposal(proposal)
    assert result.passed is False
    assert any("超过10%" in item for item in result.blockers)


def test_risk_accepts_cash_heavy_valid_proposal(session):
    instrument = add_instrument(session, "600001")
    proposal = PortfolioProposalInput(
        allocations=[
            PortfolioAllocation(
                instrument_id=instrument.id,
                horizon=Horizon.SHORT,
                target_weight=Decimal("0.05"),
                reason="evidence-backed",
            )
        ],
        cash_weight=Decimal("0.95"),
        market_regime="NEUTRAL",
        rationale="cash is valid",
    )
    assert RiskEngine(session).validate_proposal(proposal).passed is True


def test_drawdown_delever_scales_target_gross_to_sixty_percent(session):
    proposal = PortfolioProposalInput(
        allocations=[
            PortfolioAllocation(
                instrument_id=f"instrument-{index}",
                horizon=Horizon.SHORT if index < 3 else Horizon.SWING,
                target_weight=Decimal("0.10"),
                reason="drawdown test",
            )
            for index in range(8)
        ],
        cash_weight=Decimal("0.20"),
        market_regime="BEAR",
        rationale="before hard risk",
    )
    adjusted = RiskEngine(session).enforce_drawdown_target(proposal, "DELEVER")
    gross = sum((item.target_weight for item in adjusted.allocations), Decimal(0))
    assert gross == Decimal("0.600000")
    assert adjusted.cash_weight == Decimal("0.400000")


def test_drawdown_blocks_intraday_incremental_buy(session):
    instrument = add_instrument(session, "600009")
    account = session.scalar(select(Account).where(Account.name == "paper-main"))
    account.cash = Decimal("800000")
    account.high_watermark = Decimal("1000000")
    result = RiskEngine(session).validate_incremental_buy(
        account=account,
        instrument=instrument,
        horizon=Horizon.SHORT,
        target_weight=Decimal("0.05"),
        current_price=Decimal("10"),
    )
    assert result.passed is False
    assert "禁止新增买入" in result.blockers[0]
