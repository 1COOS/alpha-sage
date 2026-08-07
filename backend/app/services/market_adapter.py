from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import MarketRuleVersion


@dataclass(frozen=True)
class FeeBreakdown:
    commission: Decimal
    transfer_fee: Decimal
    stamp_tax: Decimal
    rule_version_id: str
    rule_version: str
    currency: str = "CNY"

    @property
    def tax(self) -> Decimal:
        return self.transfer_fee + self.stamp_tax


class MarketAdapter(Protocol):
    market: str
    currency: str
    timezone: ZoneInfo
    settlement_days: int
    daily_history_sources: tuple[str, ...]
    intraday_sources: tuple[str, ...]

    def local_trade_date(self, value: datetime) -> date: ...

    def lot_size(self, asset_type: str) -> int: ...

    def tick_size(self, asset_type: str) -> Decimal: ...

    def price_limit_rate(self, symbol: str, asset_type: str) -> Decimal: ...

    def calculate_fees(
        self,
        *,
        side: str,
        asset_type: str,
        trade_value: Decimal,
        trade_date: date,
    ) -> FeeBreakdown: ...


class CNMarketAdapter:
    """A-share/ETF market rules behind a stable multi-market contract."""

    market = "CN"
    currency = "CNY"
    timezone = ZoneInfo("Asia/Shanghai")
    settlement_days = 1
    daily_history_sources = ("eastmoney", "baostock")
    intraday_sources = ("eastmoney", "tencent")

    def __init__(self, session: Session):
        self.session = session

    def local_trade_date(self, value: datetime) -> date:
        if value.tzinfo is None:
            value = value.replace(tzinfo=self.timezone)
        return value.astimezone(self.timezone).date()

    @staticmethod
    def lot_size(_asset_type: str) -> int:
        return 100

    @staticmethod
    def tick_size(asset_type: str) -> Decimal:
        return Decimal("0.001") if asset_type == "ETF" else Decimal("0.01")

    @staticmethod
    def price_limit_rate(symbol: str, asset_type: str) -> Decimal:
        if asset_type == "STOCK" and symbol.startswith(("30", "68")):
            return Decimal("0.20")
        return Decimal("0.10")

    def calculate_fees(
        self,
        *,
        side: str,
        asset_type: str,
        trade_value: Decimal,
        trade_date: date,
    ) -> FeeBreakdown:
        version = self.rule_version(trade_date)
        rules = version.content
        commission_rate = Decimal(str(rules["commission_rate"]))
        minimum_commission = Decimal(str(rules["minimum_commission"]))
        commission = max(minimum_commission, trade_value * commission_rate).quantize(Decimal("0.01"))
        transfer_fee = Decimal(0)
        stamp_tax = Decimal(0)
        if asset_type == "STOCK":
            transfer_fee = (trade_value * Decimal(str(rules["stock_transfer_fee_rate"]))).quantize(Decimal("0.01"))
            if side == "SELL":
                stamp_tax = (trade_value * Decimal(str(rules["stock_sell_stamp_tax_rate"]))).quantize(Decimal("0.01"))
        return FeeBreakdown(
            commission=commission,
            transfer_fee=transfer_fee,
            stamp_tax=stamp_tax,
            rule_version_id=version.id,
            rule_version=version.version,
        )

    def rule_version(self, trade_date: date) -> MarketRuleVersion:
        version = self.session.scalar(
            select(MarketRuleVersion)
            .where(
                MarketRuleVersion.market == self.market,
                MarketRuleVersion.effective_from <= trade_date,
                or_(MarketRuleVersion.effective_to.is_(None), MarketRuleVersion.effective_to >= trade_date),
            )
            .order_by(MarketRuleVersion.effective_from.desc())
            .limit(1)
        )
        if version is None:
            raise RuntimeError(f"缺少 {trade_date.isoformat()} 生效的A股交易规则版本")
        return version
