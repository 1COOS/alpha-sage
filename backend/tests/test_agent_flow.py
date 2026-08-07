from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.config import get_settings
from app.domain.enums import RunStatus, SourceHealthStatus
from app.models import (
    Account,
    DecisionRevision,
    EvidenceRef,
    Instrument,
    MarketCalendarDay,
    OrderPlan,
    ResearchDossier,
    SourceHealth,
    utc_now,
)
from app.services.agent import CognitiveAgent
from app.services.artifacts import ArtifactStore
from app.services.model import FunctionModel


def _view(horizon: str, days: int) -> dict:
    return {
        "horizon": horizon,
        "action": "BUY",
        "target_weight": "0.05",
        "expected_return_low": "-0.02",
        "expected_return_high": "0.08",
        "probability_up": "0.62",
        "confidence": "0.66",
        "holding_days": days,
        "rationale": "证据与反证均已核对后的测试判断",
        "risks": ["测试风险"],
    }


def test_cognitive_agent_completes_research_opposition_decision_and_order(session, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "artifact_root", tmp_path / "artifacts")
    trade_date = date.today()
    instruments: list[Instrument] = []
    for index in range(6):
        instrument = Instrument(
            exchange="SSE" if index % 2 == 0 else "SZSE",
            symbol="510300" if index == 0 else f"60000{index}",
            name="沪深300ETF" if index == 0 else f"测试股票{index}",
            asset_type="ETF" if index == 0 else "STOCK",
            industry="宽基" if index == 0 else f"行业{index}",
            investable=True,
            tick_size=Decimal("0.001") if index == 0 else Decimal("0.01"),
            median_turnover_20d=Decimal("100000000"),
        )
        session.add(instrument)
        session.flush()
        instruments.append(instrument)
        rows = [
            {
                "symbol": instrument.symbol,
                "exchange": instrument.exchange,
                "trade_date": trade_date - timedelta(days=5 * 366),
                "open": "3",
                "high": "3.02",
                "low": "2.98",
                "close": "3.01",
                "previous_close": "3",
                "volume": "1000000",
                "turnover": "100000000",
                "available_at": utc_now(),
                "providers": ["eastmoney", "baostock"],
            }
        ]
        for offset in range(30):
            day = trade_date - timedelta(days=29 - offset)
            price = Decimal("3") + Decimal(index) + Decimal(offset) / Decimal("100")
            rows.append(
                {
                    "symbol": instrument.symbol,
                    "exchange": instrument.exchange,
                    "trade_date": day,
                    "open": price,
                    "high": price + Decimal("0.02"),
                    "low": price - Decimal("0.02"),
                    "close": price + Decimal("0.01"),
                    "previous_close": price,
                    "volume": "1000000",
                    "turnover": "100000000",
                    "available_at": utc_now(),
                    "providers": ["eastmoney", "baostock"],
                }
            )
        ArtifactStore(session).seal_rows(
            dataset="daily_bar_confirmed",
            provider="dual-source",
            rows=rows,
            available_at=utc_now(),
            metadata={"symbol": instrument.symbol, "exchange": instrument.exchange},
        )

    session.add(
        MarketCalendarDay(
            market="CN",
            trade_date=trade_date,
            is_open=True,
            source="baostock",
            available_at=utc_now(),
        )
    )
    for source_id in ("eastmoney", "baostock"):
        health = session.get(SourceHealth, source_id)
        health.status = SourceHealthStatus.HEALTHY
        health.last_checked_at = utc_now()
    evidence = EvidenceRef(
        instrument_id=instruments[0].id,
        source_id="sse",
        source_uri="https://www.sse.com.cn/test-evidence",
        title="测试公告",
        excerpt="用于集成测试的可信证据",
        published_at=utc_now(),
        fetched_at=utc_now(),
        credibility="OFFICIAL",
        content_hash="b" * 64,
        metadata_json={"test": True},
    )
    session.add(evidence)
    account = session.scalar(select(Account).where(Account.name == "paper-main"))
    account.enabled = True
    session.commit()

    def handler(purpose, _schema):
        views = [_view("SHORT", 3), _view("SWING", 15), _view("LONG", 90)]
        if purpose == "research-thesis":
            return {
                "summary": "正方测试论点",
                "catalysts": ["测试催化"],
                "supporting_claims": ["测试证据"],
                "horizon_views": views,
            }
        if purpose == "research-opposition":
            return {
                "strongest_counter_thesis": "最强反方测试论点",
                "failure_modes": ["催化未兑现"],
                "evidence_gaps": ["仍需跟踪公告"],
                "horizon_objections": {"SHORT": ["波动风险"]},
            }
        if purpose == "research-synthesis":
            return {
                "verdict": "INVEST",
                "summary": "综合正反证据后的测试结论",
                "material_new_evidence_required_for_long_reversal": ["基本面证据变化"],
                "horizon_views": views,
            }
        if purpose == "portfolio-allocation":
            return {
                "allocations": [
                    {
                        "instrument_id": instruments[0].id,
                        "horizon": "SHORT",
                        "target_weight": "0.05",
                        "reason": "确定性集成测试配置",
                    }
                ],
                "cash_weight": "0.95",
                "market_regime": "NEUTRAL",
                "rationale": "现金保留为合法结果",
            }
        raise AssertionError(f"unexpected model purpose: {purpose}")

    run = CognitiveAgent(session, FunctionModel(handler)).run_eod(trade_date)

    assert run.status == RunStatus.COMPLETED, run.blocker
    assert run.result["researched_count"] == 6
    assert session.scalar(select(func.count()).select_from(ResearchDossier)) == 6
    assert session.scalar(select(func.count()).select_from(DecisionRevision)) == 18
    order = session.scalar(select(OrderPlan))
    assert order is not None
    assert order.currency == "CNY"
    assert order.quantity > 0
