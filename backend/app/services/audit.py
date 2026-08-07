from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def append_audit(
    session: Session,
    *,
    event_type: str,
    actor: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, Any],
) -> AuditEvent:
    previous = session.scalar(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(1))
    previous_hash = previous.event_hash if previous else None
    event_hash = stable_hash(
        {
            "event_type": event_type,
            "actor": actor,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "payload": payload,
            "previous_hash": previous_hash,
        }
    )
    event = AuditEvent(
        event_type=event_type,
        actor=actor,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        previous_hash=previous_hash,
        event_hash=event_hash,
    )
    session.add(event)
    return event
