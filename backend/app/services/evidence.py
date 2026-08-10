from __future__ import annotations

import hashlib
from datetime import datetime
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.schemas import EvidenceInput
from app.models import EvidenceRef, Instrument, SystemSetting, utc_now
from app.services.artifacts import ArtifactStore
from app.temporal import to_utc


class EvidenceRejected(RuntimeError):
    pass


class TrustedEvidenceService:
    def __init__(self, session: Session, client: httpx.AsyncClient | None = None):
        self.session = session
        self.settings = get_settings()
        self.client = client or httpx.AsyncClient(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "alpha-sage/0.1"},
        )
        self.artifacts = ArtifactStore(session)

    async def close(self) -> None:
        await self.client.aclose()

    async def ingest_url(
        self,
        url: str,
        *,
        instrument: Instrument | None = None,
        source_id: str | None = None,
        published_at: datetime | None = None,
    ) -> EvidenceRef:
        host = (urlparse(url).hostname or "").lower()
        if not self._trusted(host):
            raise EvidenceRejected(f"来源域名未列入可信白名单：{host}")
        response = await self.client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        raw = response.content
        artifact = self.artifacts.archive_bytes(
            namespace="evidence",
            provider=source_id or host,
            content=raw,
            suffix="pdf" if "pdf" in content_type else "html",
            metadata={"url": str(response.url), "content_type": content_type},
        )
        if "pdf" in content_type:
            excerpt = "PDF原文已封存；数值事实必须由结构化来源确认。"
            title = str(response.url).rsplit("/", 1)[-1]
        else:
            soup = BeautifulSoup(raw, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            title = (soup.title.string.strip() if soup.title and soup.title.string else str(response.url))[:500]
            excerpt = " ".join(soup.get_text(" ", strip=True).split())[:5000]
        now = utc_now()
        evidence = EvidenceRef(
            instrument_id=instrument.id if instrument else None,
            source_id=source_id or host,
            source_uri=str(response.url),
            title=title,
            excerpt=excerpt,
            published_at=to_utc(published_at) if published_at is not None else now,
            fetched_at=now,
            credibility="OFFICIAL" if self._official(host) else "HIGH",
            content_hash=hashlib.sha256(raw).hexdigest(),
            raw_artifact_id=artifact.id,
            metadata_json={"host": host, "content_type": content_type},
        )
        self.session.add(evidence)
        self.session.commit()
        return evidence

    def add_structured(self, payload: EvidenceInput, instrument: Instrument | None = None) -> EvidenceRef:
        host = (urlparse(payload.source_uri).hostname or "").lower()
        if not self._trusted(host):
            raise EvidenceRejected(f"来源域名未列入可信白名单：{host}")
        content = f"{payload.title}\n{payload.excerpt}".encode()
        row = EvidenceRef(
            instrument_id=instrument.id if instrument else None,
            source_id=payload.source_id,
            source_uri=payload.source_uri,
            title=payload.title,
            excerpt=payload.excerpt,
            published_at=payload.published_at,
            credibility=payload.credibility,
            content_hash=hashlib.sha256(content).hexdigest(),
            metadata_json={"structured": True},
        )
        self.session.add(row)
        self.session.commit()
        return row

    def _trusted(self, host: str) -> bool:
        setting = self.session.get(SystemSetting, "trusted_media_whitelist")
        domains = (setting.value if setting else {}).get("domains", [])
        return any(host == domain or host.endswith(f".{domain}") for domain in domains)

    @staticmethod
    def _official(host: str) -> bool:
        return any(
            host == domain or host.endswith(f".{domain}")
            for domain in (
                "sse.com.cn",
                "szse.cn",
                "cninfo.com.cn",
                "csrc.gov.cn",
                "pbc.gov.cn",
                "stats.gov.cn",
                "ndrc.gov.cn",
                "miit.gov.cn",
            )
        )
