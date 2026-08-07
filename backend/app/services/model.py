from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ModelInvocation, SystemSetting
from app.services.audit import stable_hash
from app.services.secrets import SecretStore

T = TypeVar("T", bound=BaseModel)


class ModelUnavailable(RuntimeError):
    pass


class ModelBudgetExceeded(ModelUnavailable):
    pass


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
    def __init__(self, session: Session):
        self.session = session
        self.settings = get_settings()
        persisted = self.session.get(SystemSetting, "model_settings")
        model_settings = persisted.value if persisted else {}
        api_key = SecretStore.get_api_key()
        if not api_key:
            raise ModelUnavailable("未配置模型 API key")
        self.base_url = model_settings.get("base_url", self.settings.openai_base_url)
        self.api_mode = model_settings.get("api_mode", self.settings.openai_api_mode)
        self.reasoning_model = model_settings.get("reasoning_model", self.settings.reasoning_model)
        self.fast_model = model_settings.get("fast_model", self.settings.fast_model)
        self.daily_request_budget = int(model_settings.get("daily_request_budget", 100))
        self.client = OpenAI(api_key=api_key, base_url=self.base_url)
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
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        used = (
            self.session.scalar(
                select(func.count()).select_from(ModelInvocation).where(ModelInvocation.created_at >= day_start)
            )
            or 0
        )
        if used >= self.daily_request_budget:
            raise ModelBudgetExceeded(f"今日模型调用预算已用完：{used}/{self.daily_request_budget}")
        model = self.fast_model if fast else self.reasoning_model
        request_json = {"model": model, "system": system, "user": user, "mode": self.api_mode}
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
                temperature=0,
                instructions=system,
                input=user,
            )
            text = response.output_text
            response_json = response.model_dump(mode="json")
        invocation = ModelInvocation(
            run_id=run_id,
            purpose=purpose,
            provider=self.base_url,
            model=model,
            request_hash=stable_hash(request_json),
            response_hash=stable_hash(response_json),
            request_json=request_json,
            response_json=response_json,
        )
        self.session.add(invocation)
        self.session.flush()
        return text

    @staticmethod
    def _extract_json(raw: str) -> str:
        stripped = raw.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        start = min((index for index in (stripped.find("{"), stripped.find("[")) if index >= 0), default=0)
        end = max(stripped.rfind("}"), stripped.rfind("]"))
        return stripped[start : end + 1] if end >= start else stripped


class FunctionModel:
    """Deterministic model adapter used by tests and offline contract fixtures."""

    model_name = "function-model"

    def __init__(self, handler: Callable[[str, type[BaseModel] | None], Any]):
        self.handler = handler

    def complete_json(self, *, purpose: str, schema: type[T], **_: Any) -> T:
        return schema.model_validate(self.handler(purpose, schema))

    def complete_text(self, *, purpose: str, **_: Any) -> str:
        return str(self.handler(purpose, None))
