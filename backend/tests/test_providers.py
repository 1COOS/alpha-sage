import sys
from datetime import date
from types import SimpleNamespace

import httpx

from app.services.providers import BaoStockProvider, EastmoneyProvider, InstrumentSeed, TencentHistoryProvider


class FakeBaoStockQuery:
    error_code = "0"
    error_msg = ""

    def __init__(self, rows: list[list[str]]):
        self.rows = rows
        self.index = -1

    def next(self) -> bool:
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self) -> list[str]:
        return self.rows[self.index]


async def test_eastmoney_universe_fetches_stock_and_etf_segments():
    def handler(request: httpx.Request) -> httpx.Response:
        fs = request.url.params["fs"]
        if fs.startswith("m:0"):
            diff = [{"f12": "600000", "f13": 1, "f14": "浦发银行", "f26": "19991110"}]
        else:
            diff = [{"f12": "510300", "f13": 1, "f14": "沪深300ETF", "f26": "20120528"}]
        return httpx.Response(200, json={"data": {"diff": diff}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = EastmoneyProvider(client)
    try:
        rows = await provider.fetch_universe()
    finally:
        await provider.close()

    assert {(row.symbol, row.asset_type) for row in rows} == {
        ("600000", "STOCK"),
        ("510300", "ETF"),
    }


async def test_tencent_history_normalizes_unadjusted_volume_to_shares():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "",
                "data": {
                    "sh600000": {
                        "day": [
                            ["2026-08-05", "10.00", "10.10", "10.20", "9.90", "1234"],
                        ]
                    }
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TencentHistoryProvider(client)
    seed = InstrumentSeed("SSE", "600000", "浦发银行", "STOCK", date(1999, 11, 10))
    try:
        rows = await provider.fetch_daily_bars(seed, date(2026, 8, 1), date(2026, 8, 6))
    finally:
        await provider.close()

    assert len(rows) == 1
    assert rows[0].provider == "tencent-history"
    assert rows[0].volume == 123400


async def test_baostock_explicit_session_reuses_one_login(monkeypatch):
    calls = {"login": 0, "logout": 0}

    def login():
        calls["login"] += 1
        return SimpleNamespace(error_code="0", error_msg="")

    def logout():
        calls["logout"] += 1
        return SimpleNamespace(error_code="0", error_msg="")

    fake_baostock = SimpleNamespace(
        login=login,
        logout=logout,
        query_trade_dates=lambda **_kwargs: FakeBaoStockQuery([["2026-08-05", "1"]]),
        query_history_k_data_plus=lambda *_args, **_kwargs: FakeBaoStockQuery(
            [["2026-08-05", "10.00", "10.20", "9.90", "10.10", "9.95", "123400", "1240000", "1"]]
        ),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake_baostock)
    monkeypatch.setattr(BaoStockProvider, "_session_users", 0)

    provider = BaoStockProvider()
    seed = InstrumentSeed("SSE", "600000", "浦发银行", "STOCK", date(1999, 11, 10))
    await provider.open()
    try:
        calendar = await provider.fetch_calendar(date(2026, 8, 1), date(2026, 8, 6))
        bars = await provider.fetch_daily_bars(seed, date(2026, 8, 1), date(2026, 8, 6))
    finally:
        await provider.close()

    assert calendar == [(date(2026, 8, 5), True)]
    assert len(bars) == 1
    assert calls == {"login": 1, "logout": 1}
