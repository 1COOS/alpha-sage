from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import ClassVar, Protocol, TypeVar
from zoneinfo import ZoneInfo

import httpx

from app.config import get_settings

SHANGHAI = ZoneInfo("Asia/Shanghai")
T = TypeVar("T")


@dataclass(frozen=True)
class InstrumentSeed:
    exchange: str
    symbol: str
    name: str
    asset_type: str
    listed_on: date | None


@dataclass(frozen=True)
class Bar:
    symbol: str
    exchange: str
    trade_date: date
    observed_at: datetime
    available_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    previous_close: Decimal | None
    volume: Decimal
    turnover: Decimal
    provider: str
    interval_minutes: int | None = None

    def as_row(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SpotQuote:
    symbol: str
    exchange: str
    observed_at: datetime
    price: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    previous_close: Decimal
    volume: Decimal
    turnover: Decimal
    provider: str


class MarketDataProvider(Protocol):
    async def fetch_daily_bars(self, seed: InstrumentSeed, start: date, end: date) -> list[Bar]: ...


def eastmoney_secid(seed: InstrumentSeed) -> str:
    prefix = "1" if seed.exchange == "SSE" else "0"
    return f"{prefix}.{seed.symbol}"


class EastmoneyProvider:
    provider_id = "eastmoney"

    def __init__(self, client: httpx.AsyncClient | None = None):
        settings = get_settings()
        self.client = client or httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            headers={"User-Agent": "alpha-sage/0.1"},
        )
        self.base_url = settings.eastmoney_base_url
        self.list_url = settings.eastmoney_list_url

    async def close(self) -> None:
        await self.client.aclose()

    async def fetch_universe(self, max_pages: int | None = None) -> list[InstrumentSeed]:
        stock_rows = await self._fetch_universe_segment(
            fs="m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            asset_type="STOCK",
            max_pages=max_pages,
        )
        etf_rows = await self._fetch_universe_segment(
            fs="b:MK0021,b:MK0022,b:MK0023,b:MK0024",
            asset_type="ETF",
            max_pages=max_pages,
        )
        # ETF boards can overlap with the broad security list. Prefer the
        # explicit ETF classification and keep a stable first-seen order.
        merged: dict[tuple[str, str], InstrumentSeed] = {(row.exchange, row.symbol): row for row in stock_rows}
        for row in etf_rows:
            merged[(row.exchange, row.symbol)] = row
        return list(merged.values())

    async def _fetch_universe_segment(
        self,
        *,
        fs: str,
        asset_type: str,
        max_pages: int | None,
    ) -> list[InstrumentSeed]:
        rows: list[InstrumentSeed] = []
        page = 1
        while True:
            response = await self.client.get(
                f"{self.list_url}/api/qt/clist/get",
                params={
                    "pn": page,
                    "pz": 500,
                    "po": 1,
                    "np": 1,
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f12",
                    "fs": fs,
                    "fields": "f12,f13,f14,f26",
                },
            )
            response.raise_for_status()
            payload = response.json()
            diff = ((payload.get("data") or {}).get("diff")) or []
            if not diff:
                break
            for item in diff:
                symbol = str(item.get("f12") or "")
                name = str(item.get("f14") or "").strip()
                market = int(item.get("f13") or 0)
                listed_raw = str(item.get("f26") or "")
                if not symbol or not name:
                    continue
                exchange = "SSE" if market == 1 else "SZSE"
                listed_on = None
                if len(listed_raw) == 8 and listed_raw.isdigit():
                    listed_on = date(int(listed_raw[:4]), int(listed_raw[4:6]), int(listed_raw[6:]))
                rows.append(InstrumentSeed(exchange, symbol, name, asset_type, listed_on))
            if len(diff) < 500 or (max_pages and page >= max_pages):
                break
            page += 1
        return rows

    async def fetch_daily_bars(self, seed: InstrumentSeed, start: date, end: date) -> list[Bar]:
        return await self._fetch_klines(seed, start, end, 101)

    async def fetch_intraday_bars(self, seed: InstrumentSeed, limit: int = 120) -> list[Bar]:
        end = datetime.now(SHANGHAI).date()
        start = end
        return await self._fetch_klines(seed, start, end, 5, limit=limit)

    async def _fetch_klines(
        self,
        seed: InstrumentSeed,
        start: date,
        end: date,
        klt: int,
        *,
        limit: int = 2000,
    ) -> list[Bar]:
        response = await self.client.get(
            f"{self.base_url}/api/qt/stock/kline/get",
            params={
                "secid": eastmoney_secid(seed),
                "klt": klt,
                "fqt": 0,
                "lmt": limit,
                "beg": start.strftime("%Y%m%d"),
                "end": end.strftime("%Y%m%d"),
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            },
        )
        response.raise_for_status()
        payload = response.json()
        klines = ((payload.get("data") or {}).get("klines")) or []
        result: list[Bar] = []
        previous: Decimal | None = None
        fetched_at = datetime.now(SHANGHAI)
        for raw in klines:
            fields = raw.split(",")
            if len(fields) < 7:
                continue
            observed_at = datetime.fromisoformat(fields[0]).replace(tzinfo=SHANGHAI)
            open_price = Decimal(fields[1])
            close_price = Decimal(fields[2])
            high_price = Decimal(fields[3])
            low_price = Decimal(fields[4])
            result.append(
                Bar(
                    symbol=seed.symbol,
                    exchange=seed.exchange,
                    trade_date=observed_at.date(),
                    observed_at=observed_at,
                    available_at=fetched_at,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    previous_close=previous,
                    volume=Decimal(fields[5]) * Decimal(100),
                    turnover=Decimal(fields[6]),
                    provider=self.provider_id,
                    interval_minutes=None if klt == 101 else klt,
                )
            )
            previous = close_price
        return result


class BaoStockProvider:
    provider_id = "baostock"
    # BaoStock keeps login state at process level. Serialize all calls and
    # reference-count explicit task sessions so a full history sync can reuse
    # one login without making isolated provider calls leak a session.
    _session_lock: ClassVar[threading.RLock] = threading.RLock()
    _session_users: ClassVar[int] = 0

    def __init__(self) -> None:
        self._session_depth = 0

    async def open(self) -> None:
        await asyncio.to_thread(self._open_sync)

    def _open_sync(self) -> None:
        import baostock as bs

        cls = type(self)
        with cls._session_lock:
            if cls._session_users == 0:
                self._login_locked(bs)
            cls._session_users += 1
            self._session_depth += 1

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        import baostock as bs

        cls = type(self)
        with cls._session_lock:
            if self._session_depth == 0:
                return
            self._session_depth -= 1
            cls._session_users -= 1
            if cls._session_users == 0:
                bs.logout()

    @staticmethod
    def _login_locked(bs: object) -> None:
        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"baostock login failed: {login.error_msg}")

    def _run_query(self, operation: Callable[[], T]) -> T:
        import baostock as bs

        cls = type(self)
        with cls._session_lock:
            temporary_session = cls._session_users == 0
            if temporary_session:
                self._login_locked(bs)
            try:
                return operation()
            finally:
                if temporary_session:
                    bs.logout()

    async def fetch_universe(self) -> list[InstrumentSeed]:
        return await asyncio.to_thread(self._fetch_universe_sync)

    def _fetch_universe_sync(self) -> list[InstrumentSeed]:
        import baostock as bs

        def query_universe() -> list[InstrumentSeed]:
            query = bs.query_stock_basic()
            if query.error_code != "0":
                raise RuntimeError(f"baostock universe failed: {query.error_msg}")
            rows: list[InstrumentSeed] = []
            while query.next():
                code, name, ipo_date, _out_date, security_type, status = query.get_row_data()
                if status != "1" or security_type not in {"1", "5"}:
                    continue
                prefix, symbol = code.split(".", 1)
                if prefix not in {"sh", "sz"}:
                    continue
                asset_type = "ETF" if security_type == "5" else "STOCK"
                if asset_type == "STOCK" and not symbol.startswith(("0", "3", "6")):
                    continue
                listed_on = date.fromisoformat(ipo_date) if ipo_date else None
                rows.append(
                    InstrumentSeed(
                        exchange="SSE" if prefix == "sh" else "SZSE",
                        symbol=symbol,
                        name=name.strip(),
                        asset_type=asset_type,
                        listed_on=listed_on,
                    )
                )
            return rows

        return self._run_query(query_universe)

    async def fetch_daily_bars(self, seed: InstrumentSeed, start: date, end: date) -> list[Bar]:
        return await asyncio.to_thread(self._fetch_daily_sync, seed, start, end)

    def _fetch_daily_sync(self, seed: InstrumentSeed, start: date, end: date) -> list[Bar]:
        import baostock as bs

        def query_daily_bars() -> list[Bar]:
            prefix = "sh" if seed.exchange == "SSE" else "sz"
            query = bs.query_history_k_data_plus(
                f"{prefix}.{seed.symbol}",
                "date,open,high,low,close,preclose,volume,amount,tradestatus",
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                frequency="d",
                adjustflag="3",
            )
            if query.error_code != "0":
                raise RuntimeError(f"baostock query failed: {query.error_msg}")
            fetched_at = datetime.now(SHANGHAI)
            result: list[Bar] = []
            while query.next():
                row = query.get_row_data()
                if row[8] != "1" or not all(row[index] for index in range(1, 8)):
                    continue
                trade_date = date.fromisoformat(row[0])
                result.append(
                    Bar(
                        symbol=seed.symbol,
                        exchange=seed.exchange,
                        trade_date=trade_date,
                        observed_at=datetime.combine(trade_date, datetime.min.time(), SHANGHAI),
                        available_at=fetched_at,
                        open=Decimal(row[1]),
                        high=Decimal(row[2]),
                        low=Decimal(row[3]),
                        close=Decimal(row[4]),
                        previous_close=Decimal(row[5]),
                        volume=Decimal(row[6]),
                        turnover=Decimal(row[7]),
                        provider=self.provider_id,
                    )
                )
            return result

        return self._run_query(query_daily_bars)

    async def fetch_calendar(self, start: date, end: date) -> list[tuple[date, bool]]:
        return await asyncio.to_thread(self._fetch_calendar_sync, start, end)

    def _fetch_calendar_sync(self, start: date, end: date) -> list[tuple[date, bool]]:
        import baostock as bs

        def query_calendar() -> list[tuple[date, bool]]:
            query = bs.query_trade_dates(start_date=start.isoformat(), end_date=end.isoformat())
            if query.error_code != "0":
                raise RuntimeError(f"baostock calendar failed: {query.error_msg}")
            rows: list[tuple[date, bool]] = []
            while query.next():
                raw_date, is_trading = query.get_row_data()
                rows.append((date.fromisoformat(raw_date), is_trading == "1"))
            return rows

        return self._run_query(query_calendar)


class TencentQuoteProvider:
    provider_id = "tencent"

    def __init__(self, client: httpx.AsyncClient | None = None):
        settings = get_settings()
        self.client = client or httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            headers={"User-Agent": "alpha-sage/0.1"},
        )
        self.base_url = settings.tencent_quote_url

    async def close(self) -> None:
        await self.client.aclose()

    async def fetch_spot(self, seed: InstrumentSeed) -> SpotQuote:
        prefix = "sh" if seed.exchange == "SSE" else "sz"
        response = await self.client.get(self.base_url, params={"q": f"{prefix}{seed.symbol}"})
        response.raise_for_status()
        text = response.content.decode("gbk", errors="replace")
        if '="' not in text:
            raise RuntimeError("unexpected Tencent quote response")
        payload = text.split('="', 1)[1].rsplit('"', 1)[0]
        fields = payload.split("~")
        if len(fields) < 38 or not fields[3]:
            raise RuntimeError("Tencent quote missing required fields")
        observed_at = datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI)
        return SpotQuote(
            symbol=seed.symbol,
            exchange=seed.exchange,
            observed_at=observed_at,
            price=Decimal(fields[3]),
            previous_close=Decimal(fields[4]),
            open=Decimal(fields[5]),
            high=Decimal(fields[33]),
            low=Decimal(fields[34]),
            volume=Decimal(fields[36] or "0"),
            turnover=Decimal(fields[37] or "0"),
            provider=self.provider_id,
        )


class TencentHistoryProvider:
    provider_id = "tencent-history"

    def __init__(self, client: httpx.AsyncClient | None = None):
        settings = get_settings()
        self.client = client or httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            headers={"User-Agent": "Mozilla/5.0 AlphaSage/0.1"},
        )
        self.url = settings.tencent_history_url

    async def close(self) -> None:
        await self.client.aclose()

    async def fetch_daily_bars(self, seed: InstrumentSeed, start: date, end: date) -> list[Bar]:
        prefix = "sh" if seed.exchange == "SSE" else "sz"
        fetched_at = datetime.now(UTC)
        raw_rows: dict[date, list[str]] = {}
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(end, chunk_start + timedelta(days=600))
            response = await self.client.get(
                self.url,
                params={
                    "param": (f"{prefix}{seed.symbol},day,{chunk_start.isoformat()},{chunk_end.isoformat()},640,bfq")
                },
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 0:
                raise RuntimeError(f"Tencent history failed: {payload.get('msg') or 'unknown error'}")
            security = (payload.get("data") or {}).get(f"{prefix}{seed.symbol}") or {}
            for row in security.get("day") or []:
                if len(row) >= 6:
                    raw_rows[date.fromisoformat(row[0])] = row
            chunk_start = chunk_end + timedelta(days=1)

        result: list[Bar] = []
        previous_close: Decimal | None = None
        for trade_date in sorted(raw_rows):
            row = raw_rows[trade_date]
            open_price = Decimal(row[1])
            close_price = Decimal(row[2])
            high_price = Decimal(row[3])
            low_price = Decimal(row[4])
            volume = Decimal(row[5]) * Decimal(100)
            typical = (open_price + close_price + high_price + low_price) / Decimal(4)
            result.append(
                Bar(
                    symbol=seed.symbol,
                    exchange=seed.exchange,
                    trade_date=trade_date,
                    observed_at=datetime.combine(trade_date, datetime.min.time(), SHANGHAI),
                    available_at=fetched_at,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    previous_close=previous_close,
                    volume=volume,
                    turnover=volume * typical,
                    provider=self.provider_id,
                )
            )
            previous_close = close_price
        return result


def source_payload_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
