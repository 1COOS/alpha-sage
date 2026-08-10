from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataArtifact, Instrument, TimeCorrection, ensure_utc


class MarketRepository:
    def __init__(self, session: Session):
        self.session = session

    def confirmed_artifacts(self, instrument: Instrument) -> list[DataArtifact]:
        rows = list(
            self.session.scalars(
                select(DataArtifact)
                .where(DataArtifact.dataset == "daily_bar_confirmed")
                .order_by(DataArtifact.sealed_at.desc())
            )
        )
        return [row for row in rows if row.metadata_json.get("symbol") == instrument.symbol]

    def latest_price(self, instrument: Instrument, *, available_by: datetime | None = None) -> Decimal | None:
        rows = self.history(instrument, limit=1, available_by=available_by)
        return Decimal(str(rows[-1]["close"])) if rows else None

    def history(
        self,
        instrument: Instrument,
        *,
        start: date | None = None,
        end: date | None = None,
        limit: int | None = None,
        available_by: datetime | None = None,
    ) -> list[dict[str, Any]]:
        artifacts = self.confirmed_artifacts(instrument)
        if available_by is not None:
            artifact_ids = [row.id for row in artifacts]
            corrections = (
                {
                    entity_id: canonical_utc
                    for entity_id, canonical_utc in self.session.execute(
                        select(TimeCorrection.entity_id, TimeCorrection.canonical_utc).where(
                            TimeCorrection.entity_type == "DataArtifact",
                            TimeCorrection.field_name == "available_at",
                            TimeCorrection.entity_id.in_(artifact_ids),
                        )
                    )
                }
                if artifact_ids
                else {}
            )
            artifacts = [
                row
                for row in artifacts
                if ensure_utc(corrections.get(row.id, row.available_at)) <= ensure_utc(available_by)
            ]
        paths = [row.path for row in artifacts if Path(row.path).exists()]
        if not paths:
            return []
        connection = duckdb.connect(database=":memory:")
        escaped = ",".join(repr(path) for path in paths)
        predicates = [f"symbol = '{instrument.symbol}'"]
        if start:
            predicates.append(f"trade_date >= '{start.isoformat()}'")
        if end:
            predicates.append(f"trade_date <= '{end.isoformat()}'")
        query = (
            f"SELECT * FROM read_parquet([{escaped}], union_by_name=true) "
            f"WHERE {' AND '.join(predicates)} QUALIFY row_number() OVER "
            "(PARTITION BY symbol, trade_date ORDER BY available_at DESC) = 1 "
            "ORDER BY trade_date"
        )
        if limit:
            query = f"SELECT * FROM ({query}) ORDER BY trade_date DESC LIMIT {int(limit)}"
        frame = connection.execute(query).fetchdf()
        connection.close()
        rows = frame.to_dict(orient="records")
        return list(reversed(rows)) if limit else rows

    def price_map(self, instruments: list[Instrument], *, available_by: datetime | None = None) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for instrument in instruments:
            if price := self.latest_price(instrument, available_by=available_by):
                result[instrument.id] = price
        return result

    def benchmark_instrument(self) -> Instrument | None:
        preferred = self.session.scalar(
            select(Instrument).where(Instrument.symbol == "510300", Instrument.investable.is_(True))
        )
        if preferred:
            return preferred
        return self.session.scalar(
            select(Instrument)
            .where(Instrument.asset_type == "ETF", Instrument.investable.is_(True))
            .order_by(Instrument.median_turnover_20d.desc())
            .limit(1)
        )
