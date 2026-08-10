from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ModelInvocation, SystemSetting
from app.services.audit import stable_hash
from app.services.secrets import SecretStore
from app.temporal import beijing_day_start_utc

T = TypeVar("T", bound=BaseModel)
MODEL_FAILURE_AUDIT_ATTR = "_alpha_sage_model_failure_audit"
MODEL_CLIENT_USER_AGENT = "alpha-sage/0.1"
MODEL_ENDPOINTS = {
    "responses": "/v1/responses",
    "chat_completions": "/v1/chat/completions",
}


class ModelUnavailable(RuntimeError):
    pass


class ModelBudgetExceeded(ModelUnavailable):
    pass


@dataclass(frozen=True)
class ResolvedModelSettings:
    base_url: str
    api_mode: str
    reasoning_model: str
    fast_model: str
    daily_request_budget: int
    api_key: str = field(repr=False)
    sources: dict[str, str] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "api_mode": self.api_mode,
            "reasoning_model": self.reasoning_model,
            "fast_model": self.fast_model,
            "daily_request_budget": self.daily_request_budget,
            "api_key_configured": bool(self.api_key),
            "sources": self.sources,
        }


def resolve_model_settings(
    session: Session,
    *,
    candidate: dict[str, Any] | None = None,
    api_key_override: str | None = None,
    require_api_key: bool = True,
) -> ResolvedModelSettings:
    settings = get_settings()
    persisted = session.get(SystemSetting, "model_settings")
    database_values = persisted.value if persisted else {}
    values = candidate if candidate is not None else database_values
    source_name = "form" if candidate is not None else "database"

    def resolved(key: str, fallback: Any) -> tuple[Any, str]:
        if key in values and values[key] not in (None, ""):
            return values[key], source_name
        return fallback, "environment"

    base_url, base_url_source = resolved("base_url", settings.openai_base_url)
    api_mode, api_mode_source = resolved("api_mode", settings.openai_api_mode)
    reasoning_model, reasoning_source = resolved("reasoning_model", settings.reasoning_model)
    fast_model, fast_source = resolved("fast_model", settings.fast_model)
    budget, budget_source = resolved("daily_request_budget", 100)
    api_key = (api_key_override or "").strip() or SecretStore.get_api_key() or ""
    if not api_key and require_api_key:
        raise ModelUnavailable("未配置模型 API key")
    return ResolvedModelSettings(
        base_url=str(base_url).rstrip("/"),
        api_mode=str(api_mode),
        reasoning_model=str(reasoning_model),
        fast_model=str(fast_model),
        daily_request_budget=int(budget),
        api_key=api_key,
        sources={
            "base_url": base_url_source,
            "api_mode": api_mode_source,
            "reasoning_model": reasoning_source,
            "fast_model": fast_source,
            "daily_request_budget": budget_source,
        },
    )


class StructuredModel(Protocol):
    model_name: str

    def complete_json(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        schema: type[T],
        run_id: str | None = None,
        fast: bool = False,
    ) -> T: ...

    def complete_text(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        run_id: str | None = None,
        fast: bool = False,
    ) -> str: ...


class OpenAICompatibleModel:
    def __init__(
        self,
        session: Session,
        *,
        candidate: dict[str, Any] | None = None,
        api_key_override: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ):
        self.session = session
        resolved = resolve_model_settings(session, candidate=candidate, api_key_override=api_key_override)
        self.base_url = resolved.base_url
        self.api_mode = resolved.api_mode
        self.reasoning_model = resolved.reasoning_model
        self.fast_model = resolved.fast_model
        self.daily_request_budget = resolved.daily_request_budget
        self._api_key_for_redaction = resolved.api_key
        self.last_call_metadata: dict[str, Any] = {}
        client_options: dict[str, Any] = {
            "api_key": resolved.api_key,
            "base_url": self.base_url,
            "default_headers": {"User-Agent": MODEL_CLIENT_USER_AGENT},
        }
        if timeout_seconds is not None:
            client_options["timeout"] = timeout_seconds
        if max_retries is not None:
            client_options["max_retries"] = max_retries
        self.client = OpenAI(**client_options)
        self.model_name = self.reasoning_model

    def complete_json(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        schema: type[T],
        run_id: str | None = None,
        fast: bool = False,
    ) -> T:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        prompt = f"{user}\n\n严格只返回满足以下 JSON Schema 的 JSON，不要 Markdown：\n{schema_json}"
        last_error: Exception | None = None
        for attempt in range(2):
            raw = self.complete_text(
                purpose=f"{purpose}:attempt-{attempt + 1}",
                system=system,
                user=prompt if attempt == 0 else f"上次输出无法解析，请重新输出。\n{prompt}",
                run_id=run_id,
                fast=fast,
            )
            try:
                payload = json.loads(self._extract_json(raw))
                return schema.model_validate(payload)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
        raise ModelUnavailable(f"模型连续两次返回不符合契约的结果：{last_error}")

    def complete_text(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        run_id: str | None = None,
        fast: bool = False,
    ) -> str:
        day_start = beijing_day_start_utc()
        used = (
            self.session.scalar(
                select(func.count()).select_from(ModelInvocation).where(ModelInvocation.created_at >= day_start)
            )
            or 0
        )
        if used >= self.daily_request_budget:
            raise ModelBudgetExceeded(f"今日模型调用预算已用完：{used}/{self.daily_request_budget}")
        model = self.fast_model if fast else self.reasoning_model
        endpoint = MODEL_ENDPOINTS.get(self.api_mode, "/v1/responses")
        request_json = {
            "model": model,
            "system": system,
            "user": user,
            "mode": self.api_mode,
            "endpoint": endpoint,
            "client_user_agent": MODEL_CLIENT_USER_AGENT,
        }
        self.last_call_metadata = {
            "model": model,
            "api_mode": self.api_mode,
            "endpoint": endpoint,
            "client_user_agent": MODEL_CLIENT_USER_AGENT,
        }
        started = perf_counter()
        try:
            if self.api_mode == "chat_completions":
                response = self.client.chat.completions.create(
                    model=model,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                text = response.choices[0].message.content or ""
                response_json = response.model_dump(mode="json")
            else:
                response = self.client.responses.create(
                    model=model,
                    instructions=system,
                    input=user,
                )
                text = response.output_text
                response_json = response.model_dump(mode="json")
            latency_ms = round((perf_counter() - started) * 1000)
            request_id = self._extract_request_id(response)
            if request_id:
                response_json["request_id"] = request_id
            self.last_call_metadata |= {
                "status": "COMPLETED",
                "latency_ms": latency_ms,
                "http_status": 200,
                "request_id": request_id,
            }
            self._record_invocation(
                run_id=run_id,
                purpose=purpose,
                model=model,
                request_json=request_json,
                response_json=response_json,
                status="COMPLETED",
                latency_ms=latency_ms,
                http_status=200,
            )
            return text
        except Exception as exc:
            error_type, error_message, http_status, request_id = self.classify_error(exc)
            latency_ms = round((perf_counter() - started) * 1000)
            error_json = {
                "error_type": error_type,
                "message": error_message,
                "http_status": http_status,
                "request_id": request_id,
                "endpoint": endpoint,
                "client_user_agent": MODEL_CLIENT_USER_AGENT,
            }
            self.last_call_metadata |= {
                "status": "FAILED",
                "latency_ms": latency_ms,
                "http_status": http_status,
                "request_id": request_id,
                "error_type": error_type,
            }
            invocation = self._record_invocation(
                run_id=run_id,
                purpose=purpose,
                model=model,
                request_json=request_json,
                response_json=error_json,
                status="FAILED",
                latency_ms=latency_ms,
                http_status=http_status,
                error_type=error_type,
                error_message=error_message,
            )
            setattr(exc, MODEL_FAILURE_AUDIT_ATTR, self._invocation_snapshot(invocation))
            raise

    def _record_invocation(
        self,
        *,
        run_id: str | None,
        purpose: str,
        model: str,
        request_json: dict[str, Any],
        response_json: dict[str, Any],
        status: str,
        latency_ms: int,
        http_status: int | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> ModelInvocation:
        invocation = ModelInvocation(
            run_id=run_id,
            purpose=purpose,
            provider=self.base_url,
            model=model,
            request_hash=stable_hash(request_json),
            response_hash=stable_hash(response_json),
            request_json=request_json,
            response_json=response_json,
            status=status,
            latency_ms=latency_ms,
            http_status=http_status,
            error_type=error_type,
            error_message=error_message,
        )
        self.session.add(invocation)
        self.session.flush()
        return invocation

    def classify_error(self, exc: Exception) -> tuple[str, str, int | None, str | None]:
        return self._classify_error(exc, secrets=(self._api_key_for_redaction,))

    @staticmethod
    def _classify_error(
        exc: Exception,
        *,
        secrets: tuple[str, ...] = (),
    ) -> tuple[str, str, int | None, str | None]:
        raw = str(exc).strip() or exc.__class__.__name__
        message = OpenAICompatibleModel._sanitize_error_message(raw, secrets=secrets)
        lowered = message.lower()
        class_name = exc.__class__.__name__.lower()
        http_status = getattr(exc, "status_code", None)
        if "blocked" in lowered:
            error_type = "provider_blocked"
        elif "authentication" in class_name or http_status == 401:
            error_type = "authentication"
        elif "permission" in class_name or http_status == 403:
            error_type = "permission"
        elif "timeout" in class_name:
            error_type = "timeout"
        elif "connection" in class_name:
            error_type = "network"
        elif "ratelimit" in class_name or http_status == 429:
            error_type = "rate_limit"
        elif "notfound" in class_name or http_status == 404 or "model not found" in lowered:
            error_type = "model_not_found"
        elif http_status == 400:
            error_type = "bad_request"
        else:
            error_type = "provider_error"
        normalized_status = http_status if isinstance(http_status, int) else None
        if error_type == "provider_blocked" and normalized_status == 403:
            message = f"New API 前置代理/WAF 拦截客户端请求；这不表示 API Key 无效。上游信息：{message}"
        return error_type, message, normalized_status, OpenAICompatibleModel._extract_request_id(exc)

    @staticmethod
    def _extract_request_id(source: Any) -> str | None:
        candidates = [getattr(source, "request_id", None), getattr(source, "_request_id", None)]
        response = getattr(source, "response", None)
        headers = getattr(response, "headers", None) or getattr(source, "headers", None)
        if headers is not None:
            for header in ("x-request-id", "request-id", "x-new-api-request-id", "cf-ray"):
                try:
                    candidates.append(headers.get(header))
                except (AttributeError, TypeError):
                    break
        for value in candidates:
            if value:
                cleaned = re.sub(r"[^A-Za-z0-9._:-]", "", str(value))[:160]
                if cleaned:
                    return cleaned
        return None

    @staticmethod
    def _sanitize_error_message(raw: str, *, secrets: tuple[str, ...] = ()) -> str:
        message = raw.replace("\n", " ")
        for secret in secrets:
            if secret:
                message = message.replace(secret, "[REDACTED]")
        patterns = (
            (r"(?i)bearer\s+[a-z0-9._~-]+", "Bearer [REDACTED]"),
            (r"(?i)\bsk-[a-z0-9_-]{8,}\b", "[REDACTED]"),
            (
                r"(?i)\b(authorization|api[-_ ]?key|token)\s*[:=]\s*[^\s,;]+",
                r"\1=[REDACTED]",
            ),
            (r"(?i)([?&](?:api_key|key|token)=)[^&\s]+", r"\1[REDACTED]"),
        )
        for pattern, replacement in patterns:
            message = re.sub(pattern, replacement, message)
        return message[:500]

    @staticmethod
    def _invocation_snapshot(invocation: ModelInvocation) -> dict[str, Any]:
        return {
            "id": invocation.id,
            "run_id": invocation.run_id,
            "purpose": invocation.purpose,
            "provider": invocation.provider,
            "model": invocation.model,
            "request_hash": invocation.request_hash,
            "response_hash": invocation.response_hash,
            "request_json": invocation.request_json,
            "response_json": invocation.response_json,
            "status": invocation.status,
            "latency_ms": invocation.latency_ms,
            "http_status": invocation.http_status,
            "error_type": invocation.error_type,
            "error_message": invocation.error_message,
            "created_at": invocation.created_at,
        }

    @staticmethod
    def _extract_json(raw: str) -> str:
        stripped = raw.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        start = min((index for index in (stripped.find("{"), stripped.find("[")) if index >= 0), default=0)
        end = max(stripped.rfind("}"), stripped.rfind("]"))
        return stripped[start : end + 1] if end >= start else stripped


def has_model_failure(exc: Exception) -> bool:
    return isinstance(getattr(exc, MODEL_FAILURE_AUDIT_ATTR, None), dict)


def restore_failed_model_invocation(session: Session, exc: Exception) -> ModelInvocation | None:
    payload = getattr(exc, MODEL_FAILURE_AUDIT_ATTR, None)
    if not isinstance(payload, dict) or not payload.get("id"):
        return None
    existing = session.get(ModelInvocation, payload["id"])
    if existing is not None:
        return existing
    invocation = ModelInvocation(**payload)
    session.add(invocation)
    session.flush()
    return invocation


def format_run_failure(
    session: Session,
    *,
    run_id: str,
    stage: str | None,
    exc: Exception,
) -> tuple[str, dict[str, Any]]:
    invocation = session.scalar(
        select(ModelInvocation)
        .where(ModelInvocation.run_id == run_id, ModelInvocation.status == "FAILED")
        .order_by(ModelInvocation.created_at.desc())
        .limit(1)
    )
    failed_stage = stage or "RUNNING"
    if invocation is not None:
        detail = {
            "stage": failed_stage,
            "model": invocation.model,
            "purpose": invocation.purpose,
            "latency_ms": invocation.latency_ms,
            "http_status": invocation.http_status,
            "error_type": invocation.error_type,
            "message": invocation.error_message,
            "endpoint": invocation.request_json.get("endpoint"),
            "client_user_agent": invocation.request_json.get("client_user_agent"),
            "request_id": invocation.response_json.get("request_id"),
        }
        timing = f"，{invocation.latency_ms}ms" if invocation.latency_ms is not None else ""
        message = (
            f"{failed_stage} 阶段调用模型 {invocation.model} 失败"
            f"（{invocation.error_type or 'provider_error'}{timing}）：{invocation.error_message}"
        )
        return message, {"failure": detail}
    message = OpenAICompatibleModel._sanitize_error_message(str(exc) or exc.__class__.__name__)
    return f"{failed_stage} 阶段失败：{message}", {
        "failure": {
            "stage": failed_stage,
            "error_type": exc.__class__.__name__,
            "message": message,
        }
    }


class FunctionModel:
    """Deterministic model adapter used by tests and offline contract fixtures."""

    model_name = "function-model"

    def __init__(self, handler: Callable[[str, type[BaseModel] | None], Any]):
        self.handler = handler

    def complete_json(self, *, purpose: str, schema: type[T], **_: Any) -> T:
        return schema.model_validate(self.handler(purpose, schema))

    def complete_text(self, *, purpose: str, **_: Any) -> str:
        return str(self.handler(purpose, None))
