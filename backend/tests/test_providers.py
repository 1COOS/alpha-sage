from datetime import date

import httpx

from app.services.providers import EastmoneyProvider, InstrumentSeed, TencentHistoryProvider


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
