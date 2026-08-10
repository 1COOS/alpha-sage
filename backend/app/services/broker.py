from __future__ import annotations

from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import OrderStatus
from app.models import (
    Account,
    CashLedgerEntry,
    Instrument,
    OrderPlan,
    PaperFill,
    PositionLot,
    ensure_utc,
    utc_now,
)
from app.services.audit import append_audit
from app.services.market_adapter import CNMarketAdapter, MarketAdapter
from app.services.providers import Bar, SpotQuote


class FillBlocked(RuntimeError):
    pass


class PaperBroker:
    version = "paper-broker-v1"
    commission_rate = Decimal("0.0003")
    minimum_commission = Decimal("5")
    stock_sell_stamp_tax = Decimal("0.0005")
    transfer_fee_rate = Decimal("0.00001")
    slippage_rate = Decimal("0.001")

    def __init__(self, session: Session, market: MarketAdapter | None = None):
        self.session = session
        self.market = market or CNMarketAdapter(session)

    def execute(
        self,
        *,
        order: OrderPlan,
        bar: Bar,
        confirmation: SpotQuote,
        artifact_id: str,
    ) -> PaperFill:
        if order.status != OrderStatus.PENDING:
            raise FillBlocked("订单不是待成交状态")
        instrument = self.session.get(Instrument, order.instrument_id)
        account = self.session.get(Account, order.account_id)
        if instrument is None or account is None:
            raise FillBlocked("订单引用的账户或标的不存在")
        if order.currency != account.currency or instrument.currency != account.currency:
            raise FillBlocked("订单、账户与标的币种不一致")
        risk_liquidation = bool(
            order.side == "SELL" and account.paused_reason and account.paused_reason.startswith("组合回撤达到18%")
        )
        if not account.enabled and not risk_liquidation:
            raise FillBlocked("模拟账户未启用")
        if bar.symbol != instrument.symbol or confirmation.symbol != instrument.symbol:
            raise FillBlocked("成交行情与订单标的不一致")
        if abs(bar.close - confirmation.price) > instrument.tick_size:
            raise FillBlocked("5分钟行情与确认报价差异超过最小价格变动单位")
        if not self._bar_is_tradable(bar, instrument):
            raise FillBlocked("停牌、无成交或封死涨跌停，不能虚构成交")

        reference = self._vwap_or_close(bar)
        adverse = Decimal(1) + self.slippage_rate if order.side == "BUY" else Decimal(1) - self.slippage_rate
        fill_price = (reference * adverse).quantize(instrument.tick_size, rounding=ROUND_HALF_UP)
        max_lots = (bar.volume * Decimal("0.05") / instrument.lot_size).to_integral_value(rounding=ROUND_DOWN)
        max_quantity = int(max_lots) * instrument.lot_size
        quantity = min(order.quantity - order.filled_quantity, max_quantity)
        quantity = quantity // instrument.lot_size * instrument.lot_size
        if quantity <= 0:
            raise FillBlocked("下一根5分钟行情可用成交量不足")

        trade_value = fill_price * quantity
        fees = self.market.calculate_fees(
            side=order.side,
            asset_type=instrument.asset_type,
            trade_value=trade_value,
            trade_date=bar.trade_date,
        )
        commission = fees.commission
        tax = fees.tax

        if order.side == "BUY":
            total = trade_value + commission + tax
            if account.cash < total:
                raise FillBlocked("可用现金不足")
            account.cash -= total
        else:
            self._consume_sellable_lots(order, quantity, bar.trade_date)
            total = trade_value - commission - tax
            account.cash += total

        fill = PaperFill(
            order_id=order.id,
            account_id=account.id,
            currency=account.currency,
            instrument_id=instrument.id,
            horizon=order.horizon,
            side=order.side,
            quantity=quantity,
            reference_price=reference,
            fill_price=fill_price,
            commission=commission,
            tax=tax,
            slippage_bps=10,
            market_rule_version_id=fees.rule_version_id,
            bar_artifact_id=artifact_id,
            local_trade_date=bar.trade_date,
            filled_at=ensure_utc(bar.observed_at),
        )
        self.session.add(fill)
        self.session.flush()
        if order.side == "BUY":
            self.session.add(
                PositionLot(
                    account_id=account.id,
                    instrument_id=instrument.id,
                    horizon=order.horizon,
                    opened_fill_id=fill.id,
                    opened_trade_date=bar.trade_date,
                    quantity=quantity,
                    remaining_quantity=quantity,
                    cost_price=(trade_value + commission + tax) / quantity,
                )
            )
        order.filled_quantity += quantity
        order.status = OrderStatus.FILLED if order.filled_quantity >= order.quantity else OrderStatus.PARTIALLY_FILLED
        self.session.add(
            CashLedgerEntry(
                account_id=account.id,
                event_type=f"PAPER_{order.side}",
                amount=-total if order.side == "BUY" else total,
                balance_after=account.cash,
                reference_type="PaperFill",
                reference_id=fill.id,
                occurred_at=ensure_utc(bar.observed_at),
            )
        )
        append_audit(
            self.session,
            event_type="PAPER_FILL",
            actor="paper-broker",
            entity_type="PaperFill",
            entity_id=fill.id,
            payload={
                "order_id": order.id,
                "side": order.side,
                "quantity": quantity,
                "fill_price": str(fill_price),
                "commission": str(commission),
                "tax": str(tax),
                "currency": account.currency,
                "market_rule_version": fees.rule_version,
            },
        )
        self.session.commit()
        return fill

    def _consume_sellable_lots(self, order: OrderPlan, quantity: int, trade_date: date) -> None:
        lots = list(
            self.session.scalars(
                select(PositionLot)
                .where(
                    PositionLot.account_id == order.account_id,
                    PositionLot.instrument_id == order.instrument_id,
                    PositionLot.horizon == order.horizon,
                    PositionLot.remaining_quantity > 0,
                    PositionLot.opened_trade_date < trade_date,
                )
                .order_by(PositionLot.opened_trade_date, PositionLot.id)
            )
        )
        available = sum(lot.remaining_quantity for lot in lots)
        if available < quantity:
            raise FillBlocked("T+1可卖数量不足")
        remaining = quantity
        for lot in lots:
            consumed = min(lot.remaining_quantity, remaining)
            lot.remaining_quantity -= consumed
            remaining -= consumed
            if lot.remaining_quantity == 0:
                lot.closed_at = utc_now()
            if remaining == 0:
                break

    @staticmethod
    def _vwap_or_close(bar: Bar) -> Decimal:
        if bar.volume > 0 and bar.turnover > 0:
            candidate = bar.turnover / bar.volume
            if bar.low <= candidate <= bar.high:
                return candidate
        return bar.close

    def _bar_is_tradable(self, bar: Bar, instrument: Instrument) -> bool:
        if bar.volume <= 0 or bar.turnover <= 0 or min(bar.open, bar.high, bar.low, bar.close) <= 0:
            return False
        previous = bar.previous_close
        if not previous:
            return True
        limit_rate = self.market.price_limit_rate(instrument.symbol, instrument.asset_type)
        upper = previous * (Decimal(1) + limit_rate)
        lower = previous * (Decimal(1) - limit_rate)
        locked_up = bar.low == bar.high and bar.high >= upper - instrument.tick_size
        locked_down = bar.low == bar.high and bar.low <= lower + instrument.tick_size
        return not (locked_up or locked_down)
