from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import Horizon, OrderStatus
from app.domain.schemas import PortfolioProposalInput
from app.models import (
    Account,
    DecisionRevision,
    Instrument,
    OrderPlan,
    PositionLot,
    utc_now,
)

MAX_INSTRUMENT = Decimal("0.10")
MAX_INDUSTRY = Decimal("0.25")
MAX_GROSS = Decimal("0.85")
MIN_CASH = Decimal("0.15")
DELEVER_TARGET = Decimal("0.60")
HORIZON_BOUNDS = {
    Horizon.SHORT: (Decimal("0.10"), Decimal("0.30")),
    Horizon.SWING: (Decimal("0.30"), Decimal("0.50")),
    Horizon.LONG: (Decimal("0.30"), Decimal("0.50")),
}


@dataclass(frozen=True)
class RiskDecision:
    passed: bool
    blockers: list[str]
    warnings: list[str]


class RiskEngine:
    version = "risk-v1"

    def __init__(self, session: Session):
        self.session = session

    def validate_proposal(self, proposal: PortfolioProposalInput) -> RiskDecision:
        blockers: list[str] = []
        warnings: list[str] = []
        by_instrument: dict[str, Decimal] = defaultdict(Decimal)
        by_horizon: dict[Horizon, Decimal] = defaultdict(Decimal)
        by_industry: dict[str, Decimal] = defaultdict(Decimal)

        instruments = {
            item.id: item
            for item in self.session.scalars(
                select(Instrument).where(
                    Instrument.id.in_({allocation.instrument_id for allocation in proposal.allocations})
                )
            )
        }
        for allocation in proposal.allocations:
            instrument = instruments.get(allocation.instrument_id)
            if instrument is None or not instrument.investable:
                blockers.append(f"不可投资标的：{allocation.instrument_id}")
                continue
            weight = Decimal(allocation.target_weight)
            by_instrument[allocation.instrument_id] += weight
            by_horizon[allocation.horizon] += weight
            by_industry[instrument.industry or "UNCLASSIFIED"] += weight

        for instrument_id, weight in by_instrument.items():
            if weight > MAX_INSTRUMENT:
                blockers.append(f"{instrument_id} 合并仓位 {weight:.2%} 超过10%")
        for industry, weight in by_industry.items():
            if weight > MAX_INDUSTRY:
                blockers.append(f"行业 {industry} 仓位 {weight:.2%} 超过25%")
        gross = sum(by_instrument.values(), Decimal(0))
        if gross > MAX_GROSS or Decimal(proposal.cash_weight) < MIN_CASH:
            blockers.append("总仓位超过85%或现金不足15%")
        for horizon, weight in by_horizon.items():
            lower, upper = HORIZON_BOUNDS[horizon]
            if weight > upper:
                blockers.append(f"{horizon} 周期预算 {weight:.2%} 超过上限 {upper:.2%}")
            elif weight < lower and gross >= Decimal("0.60"):
                warnings.append(f"{horizon} 周期预算低于锚点允许区间")
        if len(by_instrument) > 10:
            blockers.append("组合持仓数量超过10个")
        return RiskDecision(not blockers, blockers, warnings)

    def enforce_drawdown_target(
        self,
        proposal: PortfolioProposalInput,
        risk_state: str,
    ) -> PortfolioProposalInput:
        if risk_state != "DELEVER":
            return proposal
        gross = sum((Decimal(item.target_weight) for item in proposal.allocations), Decimal(0))
        if gross <= DELEVER_TARGET or gross <= 0:
            return proposal
        scale = DELEVER_TARGET / gross
        allocations = [
            item.model_copy(
                update={"target_weight": (Decimal(item.target_weight) * scale).quantize(Decimal("0.000001"))}
            )
            for item in proposal.allocations
        ]
        adjusted_gross = sum((Decimal(item.target_weight) for item in allocations), Decimal(0))
        return proposal.model_copy(
            update={
                "allocations": allocations,
                "cash_weight": Decimal(1) - adjusted_gross,
                "rationale": proposal.rationale + "；组合回撤达到12%，硬风控将目标总仓位压至60%以内。",
            }
        )

    def validate_incremental_buy(
        self,
        *,
        account: Account,
        instrument: Instrument,
        horizon: Horizon,
        target_weight: Decimal,
        current_price: Decimal,
    ) -> RiskDecision:
        rows = list(
            self.session.execute(
                select(
                    PositionLot.instrument_id,
                    PositionLot.horizon,
                    func.sum(PositionLot.remaining_quantity),
                    func.sum(PositionLot.remaining_quantity * PositionLot.cost_price),
                )
                .where(PositionLot.account_id == account.id, PositionLot.remaining_quantity > 0)
                .group_by(PositionLot.instrument_id, PositionLot.horizon)
            )
        )

        def row_value(row) -> Decimal:
            if row[0] == instrument.id:
                return Decimal(row[2] or 0) * current_price
            return Decimal(row[3] or 0)

        gross = sum((row_value(row) for row in rows), Decimal(0))
        equity = account.cash + gross
        if equity <= 0:
            return RiskDecision(False, ["账户权益不可用"], [])
        drawdown, state = self.drawdown_state(account, equity)
        if state != "NORMAL":
            return RiskDecision(False, [f"组合回撤 {drawdown:.2%}，禁止新增买入"], [])

        current_horizon = sum(
            (row_value(row) for row in rows if row[0] == instrument.id and row[1] == horizon),
            Decimal(0),
        )
        current_instrument = sum(
            (row_value(row) for row in rows if row[0] == instrument.id),
            Decimal(0),
        )
        target_value = equity * target_weight
        if target_value <= current_horizon:
            return RiskDecision(True, [], [])
        projected_instrument = current_instrument - current_horizon + target_value
        projected_gross = gross - current_horizon + target_value
        blockers: list[str] = []
        if projected_instrument / equity > MAX_INSTRUMENT:
            blockers.append("盘中修订将使单标的合并仓位超过10%")
        if projected_gross / equity > MAX_GROSS:
            blockers.append("盘中修订将使总仓位超过85%")

        ids = {row[0] for row in rows}
        instruments = {item.id: item for item in self.session.scalars(select(Instrument).where(Instrument.id.in_(ids)))}
        industry_value = sum(
            (
                row_value(row)
                for row in rows
                if (instruments.get(row[0]) and instruments[row[0]].industry == instrument.industry)
            ),
            Decimal(0),
        )
        projected_industry = industry_value - current_horizon + target_value
        if instrument.industry and projected_industry / equity > MAX_INDUSTRY:
            blockers.append("盘中修订将使行业仓位超过25%")
        return RiskDecision(not blockers, blockers, [])

    def drawdown_state(self, account: Account, equity: Decimal) -> tuple[Decimal, str]:
        if equity > account.high_watermark:
            account.high_watermark = equity
        if account.high_watermark <= 0:
            return Decimal(0), "NORMAL"
        drawdown = (account.high_watermark - equity) / account.high_watermark
        if drawdown >= Decimal("0.18"):
            return drawdown, "PAUSE_NEW_ORDERS"
        if drawdown >= Decimal("0.12"):
            return drawdown, "DELEVER"
        return drawdown, "NORMAL"

    def build_orders(
        self,
        *,
        account: Account,
        proposal: PortfolioProposalInput,
        decisions: dict[tuple[str, Horizon], DecisionRevision],
        prices: dict[str, Decimal],
    ) -> list[OrderPlan]:
        risk = self.validate_proposal(proposal)
        if not risk.passed:
            raise ValueError("; ".join(risk.blockers))
        equity = account.cash + self.current_market_value(prices, account.id)
        current: dict[tuple[str, Horizon], int] = defaultdict(int)
        rows = self.session.execute(
            select(PositionLot.instrument_id, PositionLot.horizon, func.sum(PositionLot.remaining_quantity))
            .where(PositionLot.account_id == account.id, PositionLot.remaining_quantity > 0)
            .group_by(PositionLot.instrument_id, PositionLot.horizon)
        )
        for instrument_id, horizon, quantity in rows:
            current[(instrument_id, Horizon(horizon))] = int(quantity or 0)

        plans: list[OrderPlan] = []
        targets = {(item.instrument_id, item.horizon): Decimal(item.target_weight) for item in proposal.allocations}
        keys = set(current) | set(targets)
        instruments = {
            item.id: item
            for item in self.session.scalars(select(Instrument).where(Instrument.id.in_({key[0] for key in keys})))
        }
        for instrument_id, horizon in keys:
            price = prices.get(instrument_id)
            instrument = instruments.get(instrument_id)
            decision = decisions.get((instrument_id, horizon))
            if not price or not instrument or not decision:
                continue
            target_value = equity * targets.get((instrument_id, horizon), Decimal(0))
            target_lots = (target_value / price / instrument.lot_size).to_integral_value(rounding=ROUND_DOWN)
            target_qty = int(target_lots) * instrument.lot_size
            delta = target_qty - current.get((instrument_id, horizon), 0)
            if delta == 0:
                continue
            plans.append(
                OrderPlan(
                    account_id=account.id,
                    currency=account.currency,
                    decision_id=decision.id,
                    instrument_id=instrument_id,
                    horizon=horizon,
                    side="BUY" if delta > 0 else "SELL",
                    quantity=abs(delta),
                    status=OrderStatus.PENDING,
                    eligible_after=utc_now(),
                )
            )
        return plans

    def current_market_value(self, prices: dict[str, Decimal], account_id: str) -> Decimal:
        rows = self.session.execute(
            select(PositionLot.instrument_id, func.sum(PositionLot.remaining_quantity))
            .where(PositionLot.account_id == account_id, PositionLot.remaining_quantity > 0)
            .group_by(PositionLot.instrument_id)
        )
        return sum(
            (prices.get(instrument_id, Decimal(0)) * int(quantity or 0) for instrument_id, quantity in rows),
            Decimal(0),
        )
