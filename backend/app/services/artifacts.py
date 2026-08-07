from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DataArtifact, utc_now


class ArtifactStore:
    def __init__(self, session: Session):
        self.session = session
        self.settings = get_settings()

    def seal_rows(
        self,
        *,
        dataset: str,
        provider: str,
        rows: list[dict[str, Any]],
        available_at: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> DataArtifact:
        if not rows:
            raise ValueError(f"cannot seal empty dataset: {dataset}/{provider}")
        normalized = [self._normalize_row(row) for row in rows]
        table = pa.Table.from_pylist(normalized)
        stamp = utc_now().strftime("%Y%m%dT%H%M%S%f")
        directory = self.settings.artifact_root / dataset / f"provider={provider}"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stamp}.parquet"
        pq.write_table(table, path, compression="zstd")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        dates = [self._extract_date(row) for row in normalized]
        artifact = DataArtifact(
            dataset=dataset,
            provider=provider,
            path=str(path),
            sha256=digest,
            rows=len(rows),
            min_date=min((item for item in dates if item), default=None),
            max_date=max((item for item in dates if item), default=None),
            available_at=available_at,
            metadata_json=metadata or {},
        )
        self.session.add(artifact)
        self.session.flush()
        return artifact

    def archive_bytes(
        self,
        *,
        namespace: str,
        provider: str,
        content: bytes,
        suffix: str,
        metadata: dict[str, Any] | None = None,
    ) -> DataArtifact:
        digest = hashlib.sha256(content).hexdigest()
        directory = self.settings.raw_data_root / namespace / provider
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{digest}.{suffix.lstrip('.')}"
        if not path.exists():
            path.write_bytes(content)
        artifact = DataArtifact(
            dataset=f"raw_{namespace}",
            provider=provider,
            path=str(path),
            sha256=digest,
            rows=1,
            available_at=utc_now(),
            metadata_json=metadata or {},
        )
        self.session.add(artifact)
        self.session.flush()
        return artifact

    @staticmethod
    def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, (datetime, date)):
                normalized[key] = value.isoformat()
            elif hasattr(value, "as_tuple"):
                normalized[key] = str(value)
            elif isinstance(value, (dict, list)):
                normalized[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
            else:
                normalized[key] = value
        return normalized

    @staticmethod
    def _extract_date(row: dict[str, Any]) -> date | None:
        raw = row.get("trade_date") or row.get("date")
        if isinstance(raw, date):
            return raw
        if isinstance(raw, str):
            try:
                return date.fromisoformat(raw[:10])
            except ValueError:
                return None
        return None


def read_rows(paths: list[str], *, where: str | None = None) -> list[dict[str, Any]]:
    if not paths:
        return []
    import duckdb

    connection = duckdb.connect(database=":memory:", read_only=False)
    escaped = ",".join(repr(str(Path(path))) for path in paths)
    query = f"SELECT * FROM read_parquet([{escaped}])"
    if where:
        query += f" WHERE {where}"
    frame = connection.execute(query).fetchdf()
    connection.close()
    return frame.to_dict(orient="records")
