from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from app.models import AgentRun
from app.services.model import OpenAICompatibleModel
from app.services.run_queue import RunProgressReporter

TEST_SYSTEM_PROMPT = "你正在执行 Alpha Sage 模型连接测试。不要解释，只返回要求的 JSON。"
TEST_USER_PROMPT = '严格返回 {"ok": true, "service": "alpha-sage"}。'


def run_model_connection_test(
    session: Session,
    run: AgentRun,
    reporter: RunProgressReporter,
    *,
    candidate: dict[str, Any],
    api_key_override: str | None,
) -> AgentRun:
    model = OpenAICompatibleModel(
        session,
        candidate=candidate,
        api_key_override=api_key_override,
        timeout_seconds=30,
        max_retries=0,
    )
    roles = (("reasoning", False, model.reasoning_model), ("fast", True, model.fast_model))
    checks: list[dict[str, Any]] = []
    for index, (role, fast, model_name) in enumerate(roles, start=1):
        reporter.update(
            f"TEST_{role.upper()}",
            f"正在测试{('推理' if role == 'reasoning' else '快速')}模型 {model_name}",
            current=index - 1,
            total=len(roles),
        )
        started = perf_counter()
        try:
            raw = model.complete_text(
                purpose=f"model-connection-test-{role}",
                system=TEST_SYSTEM_PROMPT,
                user=TEST_USER_PROMPT,
                run_id=run.id,
                fast=fast,
            )
            payload = json.loads(OpenAICompatibleModel._extract_json(raw))
            if payload.get("ok") is not True:
                raise ValueError("模型响应未通过最小 JSON 契约")
            checks.append(
                {
                    "role": role,
                    "model": model_name,
                    "status": "COMPLETED",
                    "latency_ms": round((perf_counter() - started) * 1000),
                    "http_status": model.last_call_metadata.get("http_status"),
                    "request_id": model.last_call_metadata.get("request_id"),
                    "endpoint": model.last_call_metadata.get("endpoint"),
                    "message": "连接、鉴权、模型和结构化输出均通过",
                }
            )
        except Exception as exc:  # noqa: BLE001 - test both configured models even if one fails
            error_type, message, http_status, request_id = model.classify_error(exc)
            if isinstance(exc, (json.JSONDecodeError, ValueError)):
                error_type = "invalid_response"
            checks.append(
                {
                    "role": role,
                    "model": model_name,
                    "status": "FAILED",
                    "latency_ms": round((perf_counter() - started) * 1000),
                    "error_type": error_type,
                    "http_status": http_status or model.last_call_metadata.get("http_status"),
                    "request_id": request_id or model.last_call_metadata.get("request_id"),
                    "endpoint": model.last_call_metadata.get("endpoint"),
                    "message": message,
                }
            )
        session.commit()

    passed = all(item["status"] == "COMPLETED" for item in checks)
    result = {
        "passed": passed,
        "api_mode": model.api_mode,
        "base_url": model.base_url,
        "checks": checks,
    }
    run.result = result
    run.progress_current = len(roles)
    run.progress_total = len(roles)
    if passed:
        return reporter.complete(result, "两个模型连接测试均通过")
    failed = [item["model"] for item in checks if item["status"] != "COMPLETED"]
    return reporter.fail(
        f"模型连接测试失败：{', '.join(failed)}",
        stage=run.stage,
        result=result,
    )
