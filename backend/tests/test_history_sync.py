from datetime import date

from app.services.data_sync import HistorySyncProgress, HistorySyncService
from app.services.providers import InstrumentSeed


class FakeEastmoneyProvider:
    def __init__(self, universe: list[InstrumentSeed]):
        self.universe = universe
        self.closed = False

    async def fetch_universe(self) -> list[InstrumentSeed]:
        return self.universe

    async def close(self) -> None:
        self.closed = True


class FakeBaoStockProvider:
    def __init__(self):
        self.open_count = 0
        self.close_count = 0

    async def open(self) -> None:
        self.open_count += 1

    async def close(self) -> None:
        self.close_count += 1


class FakeTencentHistoryProvider:
    def __init__(self):
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def test_history_sync_reports_throttled_progress_and_closes_session(session):
    universe = [
        InstrumentSeed("SSE", "600000", "浦发银行", "STOCK", date(1999, 11, 10)),
        InstrumentSeed("SSE", "600001", "邯郸钢铁", "STOCK", date(1998, 2, 25)),
        InstrumentSeed("SSE", "600002", "齐鲁石化", "STOCK", date(1998, 7, 8)),
    ]
    eastmoney = FakeEastmoneyProvider(universe)
    baostock = FakeBaoStockProvider()
    tencent = FakeTencentHistoryProvider()
    progress: list[HistorySyncProgress] = []
    service = HistorySyncService(
        session,
        eastmoney=eastmoney,
        baostock=baostock,
        tencent_history=tencent,
        progress=progress.append,
        progress_every=2,
    )

    async def skip_calendar(_start: date, _end: date) -> None:
        return None

    async def sync_instrument(seed: InstrumentSeed, _start: date, _end: date) -> bool:
        if seed.symbol == "600002":
            raise RuntimeError("双源冲突")
        return seed.symbol == "600000"

    service._sync_calendar = skip_calendar
    service._sync_instrument = sync_instrument

    run = await service.sync(years=5)

    instrument_progress = [item for item in progress if item.phase == "instrument"]
    assert [item.current for item in instrument_progress] == [1, 2, 3]
    assert instrument_progress[-1].confirmed == 2
    assert instrument_progress[-1].accepted == 1
    assert instrument_progress[-1].blocked == 1
    assert run.status == "COMPLETED"
    assert run.result["confirmed"] == 2
    assert run.result["accepted"] == 1
    assert baostock.open_count == 1
    assert baostock.close_count == 1
    assert eastmoney.closed is True
    assert tencent.closed is True
