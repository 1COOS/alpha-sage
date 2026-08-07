from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.services.data_sync import HistorySyncService
from app.services.providers import Bar

SHANGHAI = ZoneInfo("Asia/Shanghai")


def bar(provider: str, close: str = "10.00") -> Bar:
    observed = datetime(2026, 8, 5, 15, 0, tzinfo=SHANGHAI)
    return Bar(
        symbol="600000",
        exchange="SSE",
        trade_date=date(2026, 8, 5),
        observed_at=observed,
        available_at=observed,
        open=Decimal("9.90"),
        high=Decimal("10.10"),
        low=Decimal("9.80"),
        close=Decimal(close),
        previous_close=Decimal("9.95"),
        volume=Decimal("1000000"),
        turnover=Decimal("10000000"),
        provider=provider,
    )


def test_dual_source_accepts_prices_within_one_tick():
    rows = HistorySyncService._reconcile(
        [bar("eastmoney")],
        [bar("baostock", "10.01")],
        Decimal("0.01"),
    )
    assert len(rows) == 1
    assert rows[0]["providers"] == ["eastmoney", "baostock"]


def test_dual_source_blocks_price_conflict():
    rows = HistorySyncService._reconcile(
        [bar("eastmoney")],
        [bar("baostock", "10.02")],
        Decimal("0.01"),
    )
    assert rows == []
