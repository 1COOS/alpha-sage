from __future__ import annotations

import statistics
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import RunKind, RunStatus, SourceHealthStatus
from app.models import (
    AgentRun,
    Instrument,
    MarketCalendarDay,
    SourceHealth,
    utc_now,
)
from app.services.artifacts import ArtifactStore
from app.services.audit import append_audit
from app.services.market_adapter import CNMarketAdapter, MarketAdapter
from app.services.providers import (
    BaoStockProvider,
    Bar,
    EastmoneyProvider,
    InstrumentSeed,
    TencentHistoryProvider,
)


class DataQualityBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class HistorySyncProgress:
    phase: str
    detail: str
    current: int | None = None
    total: int | None = None
    symbol: str | None = None
    confirmed: int = 0
    accepted: int = 0
    blocked: int = 0


class HistorySyncService:
    def __init__(
        self,
        session: Session,
        *,
        eastmoney: EastmoneyProvider | None = None,
        baostock: BaoStockProvider | None = None,
        tencent_history: TencentHistoryProvider | None = None,
        market: MarketAdapter | None = None,
        progress: Callable[[HistorySyncProgress], None] | None = None,
        progress_every: int = 25,
    ):
        self.session = session
        self.eastmoney = eastmoney or EastmoneyProvider()
        self.baostock = baostock or BaoStockProvider()
        self.tencent_history = tencent_history or TencentHistoryProvider()
        self.market = market or CNMarketAdapter(session)
        self.artifacts = ArtifactStore(session)
        self.primary_history_fallbacks: list[dict[str, str]] = []
        self.progress = progress
        self.progress_every = max(1, progress_every)

    async def sync(
        self,
        *,
        years: int = 5,
        limit: int | None = None,
        run: AgentRun | None = None,
    ) -> AgentRun:
        self.primary_history_fallbacks.clear()
        if run is None:
            run = AgentRun(kind=RunKind.DATA_SYNC, status=RunStatus.RUNNING)
            self.session.add(run)
            self.session.commit()
        try:
            await self.baostock.open()
            end = datetime.now().astimezone().date()
            start = end - timedelta(days=years * 366)
            self._emit_progress(
                HistorySyncProgress(
                    phase="calendar",
                    detail=f"同步交易日历 {start.isoformat()} 至 {(end + timedelta(days=366)).isoformat()}",
                )
            )
            await self._sync_calendar(start, end + timedelta(days=366))
            self._emit_progress(HistorySyncProgress(phase="universe", detail="获取沪深股票与 ETF 证券池"))
            universe_provider = "eastmoney"
            universe_fallback_reason = None
            try:
                universe = await self.eastmoney.fetch_universe()
            except Exception as exc:  # noqa: BLE001 - BaoStock is the declared free fallback
                universe_provider = "baostock"
                universe_fallback_reason = str(exc)
                universe = await self.baostock.fetch_universe()
            universe = [seed for seed in universe if self._static_eligible(seed, end)]
            universe.sort(
                key=lambda seed: (
                    seed.symbol != "510300",
                    seed.asset_type != "STOCK",
                    seed.symbol,
                )
            )
            if limit:
                universe = universe[:limit]
            total = len(universe)
            confirmed = 0
            accepted = 0
            blocked: list[dict[str, str]] = []
            self._emit_progress(
                HistorySyncProgress(
                    phase="history",
                    detail=f"开始串行同步 {total} 个标的的五年历史并执行双源校验",
                    current=0,
                    total=total,
                )
            )
            # SQLAlchemy Session and BaoStock's process-wide login state are not
            # concurrency-safe. Process instruments serially so every artifact,
            # universe update and source call belongs to one deterministic unit.
            for index, seed in enumerate(universe, start=1):
                outcome = "已确认"
                try:
                    investable = await self._sync_instrument(seed, start, end)
                    confirmed += 1
                    if investable:
                        accepted += 1
                        outcome = "已进入精选池"
                    else:
                        outcome = "已确认，未达到精选池门槛"
                except Exception as exc:  # noqa: BLE001 - source failures are persisted
                    blocked.append({"symbol": seed.symbol, "reason": str(exc)})
                    outcome = "已阻断"
                if index == 1 or index % self.progress_every == 0 or index == total:
                    self._emit_progress(
                        HistorySyncProgress(
                            phase="instrument",
                            detail=outcome,
                            current=index,
                            total=total,
                            symbol=seed.symbol,
                            confirmed=confirmed,
                            accepted=accepted,
                            blocked=len(blocked),
                        )
                    )
            run.result = {
                "requested": len(universe),
                "confirmed": confirmed,
                "accepted": accepted,
                "blocked": blocked[:200],
                "history_start": start.isoformat(),
                "history_end": end.isoformat(),
                "universe_provider": universe_provider,
                "universe_fallback_reason": universe_fallback_reason,
                "primary_history_fallbacks": self.primary_history_fallbacks[:200],
            }
            if confirmed == 0:
                raise DataQualityBlocked("没有任何标的通过双源历史数据校验")
            run.status = RunStatus.COMPLETED
            run.finished_at = utc_now()
            run.updated_at = run.finished_at
            run.stage = "COMPLETED"
            run.progress_current = total
            run.progress_total = total
            run.progress_message = f"同步完成：确认 {confirmed}，精选 {accepted}，阻断 {len(blocked)}"
            eastmoney_status = (
                SourceHealthStatus.DEGRADED if self.primary_history_fallbacks else SourceHealthStatus.HEALTHY
            )
            eastmoney_detail = "历史同步完成"
            if self.primary_history_fallbacks:
                eastmoney_detail = f"{len(self.primary_history_fallbacks)} 个标的使用腾讯历史回退"
            if universe_provider == "eastmoney":
                eastmoney_detail += "，证券池同步完成"
            else:
                eastmoney_detail += "；证券池入口不可用，已使用BaoStock回退"
            self._set_source_health("eastmoney", eastmoney_status, eastmoney_detail)
            self._set_source_health(
                "baostock",
                SourceHealthStatus.HEALTHY,
                "历史与日历同步完成" + ("，并提供证券池回退" if universe_provider == "baostock" else ""),
            )
            if self.primary_history_fallbacks:
                self._set_source_health("tencent", SourceHealthStatus.HEALTHY, "腾讯不复权日线回退可用")
            append_audit(
                self.session,
                event_type="HISTORY_SYNC_COMPLETED",
                actor="system",
                entity_type="AgentRun",
                entity_id=run.id,
                payload=run.result,
            )
            self.session.commit()
            self._emit_progress(
                HistorySyncProgress(
                    phase="completed",
                    detail=f"同步完成：确认 {confirmed}，精选 {accepted}，阻断 {len(blocked)}",
                    current=total,
                    total=total,
                    confirmed=confirmed,
                    accepted=accepted,
                    blocked=len(blocked),
                )
            )
            return run
        except Exception as exc:
            run.status = RunStatus.BLOCKED if isinstance(exc, DataQualityBlocked) else RunStatus.FAILED
            run.blocker = str(exc)
            run.finished_at = utc_now()
            run.updated_at = run.finished_at
            run.stage = str(run.status)
            run.progress_message = str(exc)
            self._set_source_health(
                "eastmoney",
                SourceHealthStatus.DEGRADED,
                f"历史同步未完成：{exc}",
            )
            self._set_source_health(
                "baostock",
                SourceHealthStatus.DEGRADED,
                f"历史/日历同步未完成：{exc}",
            )
            self.session.commit()
            self._emit_progress(HistorySyncProgress(phase="failed", detail=f"同步未完成：{exc}"))
            return run
        finally:
            await self.baostock.close()
            await self.eastmoney.close()
            await self.tencent_history.close()

    def _emit_progress(self, progress: HistorySyncProgress) -> None:
        if self.progress is not None:
            self.progress(progress)

    async def _sync_calendar(self, start: date, end: date) -> None:
        rows = await self.baostock.fetch_calendar(start, end)
        now = utc_now()
        for trade_date, is_open in rows:
            existing = self.session.get(MarketCalendarDay, {"market": "CN", "trade_date": trade_date})
            if existing is None:
                self.session.add(
                    MarketCalendarDay(
                        market="CN",
                        trade_date=trade_date,
                        is_open=is_open,
                        source="baostock",
                        available_at=now,
                    )
                )
        self.session.flush()

    async def _sync_instrument(self, seed: InstrumentSeed, start: date, end: date) -> bool:
        primary_provider = "eastmoney"
        try:
            east_rows = await self.eastmoney.fetch_daily_bars(seed, start, end)
        except Exception as exc:  # noqa: BLE001 - declared dual-source fallback
            primary_provider = "tencent-history"
            self.primary_history_fallbacks.append({"symbol": seed.symbol, "reason": str(exc)})
            east_rows = await self.tencent_history.fetch_daily_bars(seed, start, end)
        bao_rows = await self.baostock.fetch_daily_bars(seed, start, end)
        if len(east_rows) < 120 or len(bao_rows) < 120:
            raise DataQualityBlocked("五年数据不足或上市时间不可证明")
        tick = self.market.tick_size(seed.asset_type)
        accepted_rows = self._reconcile(east_rows, bao_rows, tick)
        if len(accepted_rows) < 120:
            raise DataQualityBlocked("双源可确认交易日不足120日")
        expected_start = max(start, seed.listed_on) if seed.listed_on else start
        if accepted_rows[0]["trade_date"] > expected_start + timedelta(days=45):
            raise DataQualityBlocked(
                f"历史起点覆盖不足：期望接近 {expected_start.isoformat()}，"
                f"实际从 {accepted_rows[0]['trade_date'].isoformat()} 开始"
            )
        recent_turnover = [row["turnover"] for row in accepted_rows[-20:]]
        median_turnover = Decimal(str(statistics.median(recent_turnover)))
        threshold = Decimal("20000000") if seed.asset_type == "ETF" else Decimal("50000000")
        investable = median_turnover >= threshold

        instrument = self.session.scalar(
            select(Instrument).where(
                Instrument.market == "CN",
                Instrument.exchange == seed.exchange,
                Instrument.symbol == seed.symbol,
            )
        )
        if instrument is None:
            instrument = Instrument(
                exchange=seed.exchange,
                symbol=seed.symbol,
                name=seed.name,
                asset_type=seed.asset_type,
                listed_on=seed.listed_on,
                currency=self.market.currency,
                lot_size=self.market.lot_size(seed.asset_type),
                tick_size=tick,
            )
            self.session.add(instrument)
        instrument.median_turnover_20d = median_turnover
        instrument.investable = investable
        instrument.exclusion_reason = None if investable else "20日中位成交额未达到精选池门槛"
        instrument.source_version = f"eastmoney+baostock:{end.isoformat()}"
        instrument.available_at = utc_now()
        instrument.updated_at = utc_now()

        metadata = {"symbol": seed.symbol, "exchange": seed.exchange, "asset_type": seed.asset_type}
        self.artifacts.seal_rows(
            dataset="daily_bar_raw",
            provider=primary_provider,
            rows=[row.as_row() for row in east_rows],
            available_at=east_rows[-1].available_at,
            metadata=metadata,
        )
        self.artifacts.seal_rows(
            dataset="daily_bar_raw",
            provider="baostock",
            rows=[row.as_row() for row in bao_rows],
            available_at=bao_rows[-1].available_at,
            metadata=metadata,
        )
        self.artifacts.seal_rows(
            dataset="daily_bar_confirmed",
            provider=f"{primary_provider}+baostock",
            rows=accepted_rows,
            available_at=max(east_rows[-1].available_at, bao_rows[-1].available_at),
            metadata=metadata,
        )
        self.session.flush()
        return investable

    @staticmethod
    def _reconcile(east_rows: Iterable[Bar], bao_rows: Iterable[Bar], tick: Decimal) -> list[dict]:
        east = {row.trade_date: row for row in east_rows}
        bao = {row.trade_date: row for row in bao_rows}
        accepted: list[dict] = []
        for trade_date in sorted(set(east) & set(bao)):
            left, right = east[trade_date], bao[trade_date]
            if any(abs(getattr(left, key) - getattr(right, key)) > tick for key in ("open", "high", "low", "close")):
                continue
            if min(left.open, left.high, left.low, left.close, right.open, right.high, right.low, right.close) <= 0:
                continue
            if (
                left.low > left.high
                or not left.low <= left.open <= left.high
                or not left.low <= left.close <= left.high
            ):
                continue
            accepted.append(
                {
                    "symbol": left.symbol,
                    "exchange": left.exchange,
                    "trade_date": trade_date,
                    "open": left.open,
                    "high": left.high,
                    "low": left.low,
                    "close": left.close,
                    "previous_close": left.previous_close or right.previous_close,
                    "volume": min(left.volume, right.volume),
                    "turnover": min(left.turnover, right.turnover),
                    "available_at": max(left.available_at, right.available_at),
                    "providers": [left.provider, right.provider],
                }
            )
        return accepted

    @staticmethod
    def _static_eligible(seed: InstrumentSeed, as_of: date) -> bool:
        upper_name = seed.name.upper()
        if "ST" in upper_name or "退" in seed.name:
            return False
        if seed.asset_type == "ETF" and not seed.symbol.startswith(("15", "16", "50", "51", "52", "53", "56", "58")):
            return False
        return seed.listed_on is None or (as_of - seed.listed_on).days >= 120

    def _set_source_health(self, source_id: str, status: str, detail: str) -> None:
        row = self.session.get(SourceHealth, source_id)
        if row:
            row.status = status
            row.detail = detail
            row.last_checked_at = utc_now()
