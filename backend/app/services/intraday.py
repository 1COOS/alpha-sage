from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import DecisionAction, OrderStatus, RunKind, RunStatus
from app.models import (
    Account,
    AgentRun,
    DecisionRevision,
    EvidenceRef,
    Instrument,
    IntradayTriggerState,
    OrderPlan,
    PositionLot,
    SourceHealth,
    ensure_utc,
    utc_now,
)
from app.services.agent import CognitiveAgent
from app.services.artifacts import ArtifactStore
from app.services.broker import FillBlocked, PaperBroker
from app.services.market_adapter import CNMarketAdapter
from app.services.model import format_run_failure, has_model_failure
from app.services.providers import EastmoneyProvider, InstrumentSeed, TencentQuoteProvider
from app.services.run_queue import RunProgressReporter


class IntradayService:
    def __init__(
        self,
        session: Session,
        *,
        agent: CognitiveAgent | None = None,
        eastmoney: EastmoneyProvider | None = None,
        tencent: TencentQuoteProvider | None = None,
    ):
        self.session = session
        self._agent = agent
        self.eastmoney = eastmoney or EastmoneyProvider()
        self.tencent = tencent or TencentQuoteProvider()
        self.artifacts = ArtifactStore(session)
        self.market = CNMarketAdapter(session)
        self.broker = PaperBroker(session, self.market)

    @property
    def agent(self) -> CognitiveAgent:
        if self._agent is None:
            self._agent = CognitiveAgent(self.session)
        return self._agent

    async def run(
        self,
        trade_date: date | None = None,
        *,
        run: AgentRun | None = None,
        reporter: RunProgressReporter | None = None,
    ) -> AgentRun:
        trade_date = trade_date or datetime.now().astimezone().date()
        if run is None:
            run = AgentRun(kind=RunKind.INTRADAY, status=RunStatus.RUNNING, trade_date=trade_date)
            self.session.add(run)
            self.session.commit()
        run.trade_date = trade_date
        if reporter:
            reporter.update("PREFLIGHT", "检查账户状态与待复核标的")
        account = self.session.scalar(select(Account).where(Account.name == "paper-main"))
        risk_liquidation_only = bool(
            account
            and not account.enabled
            and account.paused_reason
            and account.paused_reason.startswith("组合回撤达到18%")
        )
        if account is None or (not account.enabled and not risk_liquidation_only):
            return self._block(run, "模拟账户未启用")
        try:
            instruments = self._watched_instruments(account.id)
            if not instruments:
                run.status = RunStatus.COMPLETED
                run.result = {"watched": 0, "revisions": 0, "fills": 0}
                run.finished_at = utc_now()
                run.updated_at = run.finished_at
                run.stage = "COMPLETED"
                run.progress_message = "没有需要复核的持仓或待成交订单"
                self.session.commit()
                return run
            total_calls = (
                self.session.scalar(
                    select(func.sum(IntradayTriggerState.call_count)).where(
                        IntradayTriggerState.trade_date == trade_date
                    )
                )
                or 0
            )
            revisions = 0
            fills = 0
            blocked: list[dict[str, str]] = []
            for index, instrument in enumerate(instruments, start=1):
                if reporter:
                    reporter.update(
                        "INTRADAY_REVIEW",
                        f"复核 {instrument.symbol} {instrument.name}",
                        current=index - 1,
                        total=len(instruments),
                    )
                seed = InstrumentSeed(
                    exchange=instrument.exchange,
                    symbol=instrument.symbol,
                    name=instrument.name,
                    asset_type=instrument.asset_type,
                    listed_on=instrument.listed_on,
                )
                try:
                    bars = await self.eastmoney.fetch_intraday_bars(seed)
                    quote = await self.tencent.fetch_spot(seed)
                    if not bars:
                        raise RuntimeError("Eastmoney 未返回5分钟行情")
                    completed = [
                        bar
                        for bar in bars
                        if ensure_utc(bar.observed_at + timedelta(minutes=bar.interval_minutes or 5)) <= utc_now()
                    ]
                    if not completed:
                        raise RuntimeError("尚无完整结束的5分钟行情")
                    latest = completed[-1]
                    artifact = self.artifacts.seal_rows(
                        dataset="intraday_bar_confirmed",
                        provider="eastmoney+tencent",
                        rows=[latest.as_row() | {"confirmation_price": quote.price}],
                        available_at=max(latest.available_at, quote.observed_at),
                        metadata={"symbol": instrument.symbol, "exchange": instrument.exchange},
                    )
                    for order in self._pending_orders(account.id, instrument.id, latest.observed_at):
                        try:
                            self.broker.execute(
                                order=order,
                                bar=latest,
                                confirmation=quote,
                                artifact_id=artifact.id,
                            )
                            fills += 1
                        except FillBlocked as exc:
                            blocked.append({"symbol": instrument.symbol, "reason": str(exc)})

                    if risk_liquidation_only:
                        continue

                    state = self._trigger_state(trade_date, instrument.id)
                    reason = self._trigger_reason(instrument, state, bars, quote.price)
                    if not reason or total_calls >= 12 or state.call_count >= 2:
                        state.last_price = quote.price
                        continue
                    if state.last_called_at and utc_now() - ensure_utc(state.last_called_at) < timedelta(minutes=15):
                        continue
                    has_new_evidence = self._has_new_evidence(instrument.id, state.last_called_at)
                    created = self.agent.revise_intraday(
                        run=run,
                        instrument=instrument,
                        trigger_reason=reason,
                        bar_context={
                            "observed_at": latest.observed_at,
                            "open": str(latest.open),
                            "high": str(latest.high),
                            "low": str(latest.low),
                            "close": str(latest.close),
                            "volume": str(latest.volume),
                            "confirmation_price": str(quote.price),
                        },
                        has_new_material_evidence=has_new_evidence,
                    )
                    order_blockers = self._orders_from_revisions(account, instrument, quote.price, created)
                    blocked.extend({"symbol": instrument.symbol, "reason": reason} for reason in order_blockers)
                    revisions += len(created)
                    state.call_count += 1
                    state.last_called_at = utc_now()
                    state.last_price = quote.price
                    state.last_reason = reason
                    total_calls += 1
                    self.session.commit()
                except Exception as exc:  # noqa: BLE001 - source failures are isolated by symbol
                    if has_model_failure(exc):
                        raise
                    blocked.append({"symbol": instrument.symbol, "reason": str(exc)})
            run.status = RunStatus.COMPLETED
            run.finished_at = utc_now()
            run.updated_at = run.finished_at
            run.stage = "COMPLETED"
            run.progress_current = len(instruments)
            run.progress_total = len(instruments)
            run.progress_message = "盘中复核完成"
            run.result = {
                "watched": len(instruments),
                "revisions": revisions,
                "fills": fills,
                "blocked": blocked,
                "daily_model_calls": total_calls,
            }
            self._health("eastmoney", "HEALTHY", "盘中5分钟行情可用")
            self._health("tencent", "HEALTHY", "盘中确认报价可用")
            self.session.commit()
            return run
        except Exception as exc:
            failed_stage = run.stage
            message, result = format_run_failure(
                self.session,
                run_id=run.id,
                stage=failed_stage,
                exc=exc,
            )
            run.status = RunStatus.FAILED
            run.blocker = message
            run.finished_at = utc_now()
            run.updated_at = run.finished_at
            run.stage = failed_stage or "FAILED"
            run.progress_message = message
            run.result = result
            self.session.commit()
            return run
        finally:
            await self.eastmoney.close()
            await self.tencent.close()

    def _watched_instruments(self, account_id: str) -> list[Instrument]:
        ids = set(
            self.session.scalars(
                select(PositionLot.instrument_id).where(
                    PositionLot.account_id == account_id, PositionLot.remaining_quantity > 0
                )
            )
        )
        ids.update(
            self.session.scalars(
                select(OrderPlan.instrument_id).where(
                    OrderPlan.account_id == account_id,
                    OrderPlan.status.in_([OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED]),
                )
            )
        )
        ids.update(
            self.session.scalars(
                select(DecisionRevision.instrument_id).order_by(DecisionRevision.created_at.desc()).limit(20)
            )
        )
        return list(self.session.scalars(select(Instrument).where(Instrument.id.in_(ids)))) if ids else []

    def _pending_orders(self, account_id: str, instrument_id: str, observed_at: datetime) -> list[OrderPlan]:
        rows = list(
            self.session.scalars(
                select(OrderPlan).where(
                    OrderPlan.account_id == account_id,
                    OrderPlan.instrument_id == instrument_id,
                    OrderPlan.status.in_([OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED]),
                )
            )
        )
        observed_utc = ensure_utc(observed_at)
        return [row for row in rows if ensure_utc(row.eligible_after) <= observed_utc]

    def _trigger_state(self, trade_date: date, instrument_id: str) -> IntradayTriggerState:
        state = self.session.scalar(
            select(IntradayTriggerState).where(
                IntradayTriggerState.trade_date == trade_date,
                IntradayTriggerState.instrument_id == instrument_id,
            )
        )
        if state is None:
            state = IntradayTriggerState(trade_date=trade_date, instrument_id=instrument_id)
            self.session.add(state)
            self.session.flush()
        return state

    @staticmethod
    def _trigger_reason(instrument: Instrument, state: IntradayTriggerState, bars, quote_price: Decimal) -> str | None:
        latest = bars[-1]
        baseline = state.last_price or latest.previous_close or latest.open
        if baseline and abs(quote_price / baseline - 1) >= Decimal("0.02"):
            return "PRICE_MOVE_2_PERCENT"
        turnovers = [Decimal(bar.turnover) for bar in bars[-20:-1] if bar.turnover > 0]
        if turnovers and latest.turnover >= Decimal(str(statistics.median(turnovers))) * Decimal("3"):
            return "VOLUME_ANOMALY_3X"
        previous = latest.previous_close
        if previous:
            rate = CNMarketAdapter.price_limit_rate(instrument.symbol, instrument.asset_type)
            upper = previous * (Decimal(1) + rate)
            lower = previous * (Decimal(1) - rate)
            if abs(upper - quote_price) <= instrument.tick_size or abs(quote_price - lower) <= instrument.tick_size:
                return "PRICE_LIMIT_NEAR"
        return None

    def _has_new_evidence(self, instrument_id: str, since: datetime | None) -> bool:
        query = select(func.count()).select_from(EvidenceRef).where(EvidenceRef.instrument_id == instrument_id)
        if since:
            query = query.where(EvidenceRef.fetched_at > since)
        return (self.session.scalar(query) or 0) > 0

    def _orders_from_revisions(
        self,
        account: Account,
        instrument: Instrument,
        price: Decimal,
        revisions: list[DecisionRevision],
    ) -> list[str]:
        blockers: list[str] = []
        positions = defaultdict(int)
        rows = self.session.execute(
            select(PositionLot.horizon, func.sum(PositionLot.remaining_quantity))
            .where(
                PositionLot.account_id == account.id,
                PositionLot.instrument_id == instrument.id,
                PositionLot.remaining_quantity > 0,
            )
            .group_by(PositionLot.horizon)
        )
        for horizon, quantity in rows:
            positions[horizon] = int(quantity or 0)
        open_lots = self.session.scalars(
            select(PositionLot).where(
                PositionLot.account_id == account.id,
                PositionLot.remaining_quantity > 0,
            )
        )
        equity_floor = account.cash + sum(Decimal(lot.cost_price) * lot.remaining_quantity for lot in open_lots)
        for revision in revisions:
            current = positions[revision.horizon]
            if revision.action in {DecisionAction.SELL}:
                target = 0
            elif revision.action in {DecisionAction.REDUCE}:
                target = current // 2
            elif revision.action in {DecisionAction.BUY, DecisionAction.HOLD}:
                target_lots = (
                    equity_floor * Decimal(revision.target_weight) / price / instrument.lot_size
                ).to_integral_value(rounding=ROUND_DOWN)
                target = int(target_lots) * instrument.lot_size
            else:
                continue
            delta = target - current
            if delta == 0:
                continue
            if delta > 0:
                if not account.enabled:
                    blockers.append("账户处于硬风控暂停状态，禁止新增买入")
                    continue
                risk = self.agent.risk.validate_incremental_buy(
                    account=account,
                    instrument=instrument,
                    horizon=revision.horizon,
                    target_weight=Decimal(revision.target_weight),
                    current_price=price,
                )
                if not risk.passed:
                    blockers.extend(risk.blockers)
                    continue
            self.session.add(
                OrderPlan(
                    account_id=account.id,
                    currency=account.currency,
                    decision_id=revision.id,
                    instrument_id=instrument.id,
                    horizon=revision.horizon,
                    side="BUY" if delta > 0 else "SELL",
                    quantity=abs(delta),
                    status=OrderStatus.PENDING,
                    eligible_after=utc_now(),
                )
            )
        return blockers

    def _health(self, source_id: str, status: str, detail: str) -> None:
        row = self.session.get(SourceHealth, source_id)
        if row:
            row.status = status
            row.detail = detail
            row.last_checked_at = utc_now()

    def _block(self, run: AgentRun, reason: str) -> AgentRun:
        run.status = RunStatus.BLOCKED
        run.blocker = reason
        run.finished_at = utc_now()
        run.updated_at = run.finished_at
        run.stage = "BLOCKED"
        run.progress_message = reason
        self.session.commit()
        return run
