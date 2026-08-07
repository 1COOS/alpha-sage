from datetime import timedelta
from decimal import Decimal

from app.config import get_settings
from app.models import Instrument, utc_now
from app.services.artifacts import ArtifactStore
from app.services.market_repository import MarketRepository


def test_history_respects_artifact_availability_time(session, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "artifact_root", tmp_path / "artifacts")
    instrument = Instrument(
        exchange="SSE",
        symbol="600010",
        name="时点测试",
        asset_type="STOCK",
        investable=True,
        tick_size=Decimal("0.01"),
    )
    session.add(instrument)
    session.flush()
    sealed_at = utc_now()
    ArtifactStore(session).seal_rows(
        dataset="daily_bar_confirmed",
        provider="dual-source",
        rows=[
            {
                "symbol": instrument.symbol,
                "exchange": instrument.exchange,
                "trade_date": "2026-08-05",
                "open": "10",
                "high": "10.2",
                "low": "9.9",
                "close": "10.1",
                "volume": "1000000",
                "turnover": "10000000",
                "available_at": sealed_at,
            }
        ],
        available_at=sealed_at,
        metadata={"symbol": instrument.symbol},
    )
    session.commit()

    repository = MarketRepository(session)
    assert repository.history(instrument, available_by=sealed_at - timedelta(seconds=1)) == []
    assert len(repository.history(instrument, available_by=sealed_at + timedelta(seconds=1))) == 1
