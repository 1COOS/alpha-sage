from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import StrategyStatus
from app.models import (
    Account,
    AgentRun,
    Instrument,
    PositionLot,
    SourceHealth,
    StrategyVersion,
)
from app.services.market_repository import MarketRepository
from app.services.preflight import PreflightService
from app.services.risk import RiskEngine


class PortfolioService:
    def __init__(self, session: Session):
        self.session = session
        self.market = MarketRepository(session)
        self.risk = RiskEngine(session)

    def overview(self) -> dict:
        account = self.session.scalar(select(Account).where(Account.name == "paper-main"))
        if account is None:
            return {"account": None, "positions": [], "equity": "0", "drawdown": "0"}
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
        instruments = (
            {
                item.id: item
                for item in self.session.scalars(select(Instrument).where(Instrument.id.in_({row[0] for row in rows})))
            }
            if rows
            else {}
        )
        positions: list[dict] = []
        market_value = Decimal(0)
        by_horizon: dict[str, Decimal] = defaultdict(Decimal)
        by_instrument: dict[str, Decimal] = defaultdict(Decimal)
        for instrument_id, horizon, quantity, total_cost in rows:
            instrument = instruments[instrument_id]
            price = self.market.latest_price(instrument) or Decimal(total_cost) / int(quantity)
            value = price * int(quantity)
            market_value += value
            by_horizon[horizon] += value
            by_instrument[instrument_id] += value
            positions.append(
                {
                    "instrument_id": instrument_id,
                    "symbol": instrument.symbol,
                    "name": instrument.name,
                    "industry": instrument.industry,
                    "horizon": horizon,
                    "quantity": int(quantity),
                    "cost": str(Decimal(total_cost) / int(quantity)),
                    "price": str(price),
                    "market_value": str(value),
                    "unrealized_pnl": str(value - Decimal(total_cost)),
                }
            )
        equity = account.cash + market_value
        previous_high_watermark = account.high_watermark
        drawdown, risk_state = self.risk.drawdown_state(account, equity)
        changed = account.high_watermark != previous_high_watermark
        if risk_state == "PAUSE_NEW_ORDERS" and account.enabled:
            account.enabled = False
            account.paused_reason = "组合回撤达到18%，硬风控自动暂停新开仓"
            changed = True
        if changed:
            self.session.commit()
        return {
            "account": {
                "id": account.id,
                "name": account.name,
                "enabled": account.enabled,
                "paused_reason": account.paused_reason,
                "cash": str(account.cash),
                "initial_cash": str(account.initial_cash),
                "currency": account.currency,
            },
            "positions": positions,
            "cash": str(account.cash),
            "market_value": str(market_value),
            "equity": str(equity),
            "drawdown": str(drawdown),
            "risk_state": risk_state,
            "horizon_values": {key: str(value) for key, value in by_horizon.items()},
            "instrument_values": {key: str(value) for key, value in by_instrument.items()},
        }

    def system_status(self) -> dict:
        portfolio = self.overview()
        champion = self.session.scalar(select(StrategyVersion).where(StrategyVersion.status == StrategyStatus.CHAMPION))
        last_run = self.session.scalar(select(AgentRun).order_by(AgentRun.started_at.desc()).limit(1))
        health = list(self.session.scalars(select(SourceHealth).order_by(SourceHealth.source_id)))
        preflight = PreflightService(self.session).run()
        return {
            "account_enabled": bool((portfolio.get("account") or {}).get("enabled")),
            "account_cash": portfolio.get("cash", "0"),
            "equity": portfolio.get("equity", "0"),
            "drawdown": portfolio.get("drawdown", "0"),
            "current_strategy": champion.version if champion else "MISSING",
            "last_run": None
            if last_run is None
            else {
                "id": last_run.id,
                "kind": last_run.kind,
                "status": last_run.status,
                "started_at": last_run.started_at,
                "finished_at": last_run.finished_at,
                "blocker": last_run.blocker,
                "result": last_run.result,
            },
            "source_health": [
                {
                    "source_id": row.source_id,
                    "role": row.role,
                    "status": row.status,
                    "last_checked_at": row.last_checked_at,
                    "detail": row.detail,
                }
                for row in health
            ],
            "blockers": [check.detail for check in preflight.checks if not check.passed],
        }
