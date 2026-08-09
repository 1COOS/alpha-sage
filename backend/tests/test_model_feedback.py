from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.domain.enums import RunKind, RunStatus
from app.models import Account, AgentRun, Instrument, ModelInvocation, ResearchDossier, SystemSetting
from app.services.agent import CognitiveAgent
from app.services.model import OpenAICompatibleModel
from app.services.model_test import run_model_connection_test
from app.services.run_queue import RunProgressReporter
from app.services.secrets import SecretStore


def _candidate() -> dict:
    return {
        "base_url": "https://provider.example/v1",
        "api_mode": "responses",
        "reasoning_model": "reasoning-test",
        "fast_model": "fast-test",
        "daily_request_budget": 100,
    }


def test_failed_model_request_is_append_only_audited_without_api_key(session):
    model = OpenAICompatibleModel(
        session,
        candidate=_candidate(),
        api_key_override="secret-test-key",
        timeout_seconds=1,
        max_retries=0,
    )

    class BlockedError(RuntimeError):
        status_code = 403
        request_id = "req-new-api-blocked"

    def raise_blocked(**_kwargs):
        raise BlockedError("Your request was blocked. api_key=secret-test-key Authorization: Bearer secret-test-key")

    model.client = SimpleNamespace(responses=SimpleNamespace(create=raise_blocked))

    with pytest.raises(BlockedError):
        model.complete_text(purpose="research-thesis", system="system", user="user", run_id=None)
    session.commit()

    invocation = session.scalar(select(ModelInvocation))
    assert invocation.status == "FAILED"
    assert invocation.http_status == 403
    assert invocation.error_type == "provider_blocked"
    assert invocation.error_message.startswith("New API 前置代理/WAF 拦截客户端请求")
    assert "secret-test-key" not in invocation.error_message
    assert "secret-test-key" not in str(invocation.request_json)
    assert invocation.request_json["endpoint"] == "/v1/responses"
    assert invocation.request_json["client_user_agent"] == "alpha-sage/0.1"
    assert invocation.response_json["request_id"] == "req-new-api-blocked"


def test_eod_provider_block_is_visible_with_stage_model_latency_and_no_dossier(session, monkeypatch):
    account = session.scalar(select(Account).where(Account.name == "paper-main"))
    account.enabled = True
    instrument = Instrument(
        exchange="SSE",
        symbol="600000",
        name="浦发银行",
        asset_type="STOCK",
        industry="银行",
        investable=True,
    )
    run = AgentRun(kind=RunKind.EOD, status=RunStatus.RUNNING, stage="STARTING")
    session.add_all([instrument, run])
    session.commit()

    model = OpenAICompatibleModel(
        session,
        candidate=_candidate(),
        api_key_override="temporary-test-key",
        timeout_seconds=1,
        max_retries=0,
    )

    class BlockedError(RuntimeError):
        status_code = 403

    def raise_blocked(**_kwargs):
        raise BlockedError("Your request was blocked.")

    model.client = SimpleNamespace(responses=SimpleNamespace(create=raise_blocked))
    agent = CognitiveAgent(session, model)
    monkeypatch.setattr(
        "app.services.agent.PreflightService.run",
        lambda _self: SimpleNamespace(passed=True, checks=[]),
    )
    monkeypatch.setattr(agent, "_market_regime", lambda _trade_date: "NEUTRAL")
    monkeypatch.setattr(agent, "_discover_opportunities", lambda _trade_date, limit: [instrument])

    result = agent.run_eod(date.today(), run=run, reporter=RunProgressReporter(session, run))

    assert result.status == RunStatus.FAILED
    assert result.stage == "RESEARCH_THESIS"
    assert result.result["failure"]["model"] == "reasoning-test"
    assert result.result["failure"]["latency_ms"] is not None
    assert result.result["failure"]["error_type"] == "provider_blocked"
    assert "New API 前置代理/WAF" in result.progress_message
    assert session.scalar(select(func.count()).select_from(ResearchDossier)) == 0


def test_model_connection_test_uses_temporary_key_without_persisting_settings(session, monkeypatch):
    captured_client_options: list[dict] = []

    class FakeResponses:
        @staticmethod
        def create(**kwargs):
            assert set(kwargs) == {"model", "instructions", "input"}
            return SimpleNamespace(
                output_text='{"ok": true, "service": "alpha-sage"}',
                model_dump=lambda mode: {"model": kwargs["model"], "ok": True},
                _request_id=f"req-{kwargs['model']}",
            )

    def fake_openai(**kwargs):
        captured_client_options.append(kwargs)
        return SimpleNamespace(responses=FakeResponses())

    monkeypatch.setattr("app.services.model.OpenAI", fake_openai)
    monkeypatch.setattr(SecretStore, "set_api_key", lambda _value: pytest.fail("测试连接不得保存 API Key"))
    run = AgentRun(kind=RunKind.MODEL_TEST, status=RunStatus.RUNNING)
    session.add(run)
    session.commit()

    result = run_model_connection_test(
        session,
        run,
        RunProgressReporter(session, run),
        candidate=_candidate(),
        api_key_override="temporary-test-key",
    )

    assert result.status == RunStatus.COMPLETED
    assert [item["role"] for item in result.result["checks"]] == ["reasoning", "fast"]
    assert captured_client_options[0]["api_key"] == "temporary-test-key"
    assert captured_client_options[0]["default_headers"] == {"User-Agent": "alpha-sage/0.1"}
    assert all(item["endpoint"] == "/v1/responses" for item in result.result["checks"])
    assert all(item["http_status"] == 200 for item in result.result["checks"])
    assert [item["request_id"] for item in result.result["checks"]] == [
        "req-reasoning-test",
        "req-fast-test",
    ]
    assert session.get(SystemSetting, "model_settings").value == {}
    assert session.scalar(select(func.count()).select_from(ModelInvocation)) == 2


def test_model_connection_test_falls_back_to_stored_key_and_reports_partial_failure(session, monkeypatch):
    captured_client_options: list[dict] = []

    class BlockedError(RuntimeError):
        status_code = 403
        request_id = "req-fast-blocked"

    class FakeResponses:
        @staticmethod
        def create(**kwargs):
            if kwargs["model"] == "fast-test":
                raise BlockedError("Your request was blocked.")
            return SimpleNamespace(
                output_text='{"ok": true, "service": "alpha-sage"}',
                model_dump=lambda mode: {"model": kwargs["model"], "ok": True},
                _request_id="req-reasoning-completed",
            )

    def fake_openai(**kwargs):
        captured_client_options.append(kwargs)
        return SimpleNamespace(responses=FakeResponses())

    monkeypatch.setattr("app.services.model.OpenAI", fake_openai)
    monkeypatch.setattr(SecretStore, "get_api_key", lambda: "stored-test-key")
    run = AgentRun(kind=RunKind.MODEL_TEST, status=RunStatus.RUNNING)
    session.add(run)
    session.commit()

    result = run_model_connection_test(
        session,
        run,
        RunProgressReporter(session, run),
        candidate=_candidate(),
        api_key_override=None,
    )

    assert captured_client_options[0]["api_key"] == "stored-test-key"
    assert captured_client_options[0]["default_headers"] == {"User-Agent": "alpha-sage/0.1"}
    assert result.status == RunStatus.FAILED
    checks = {item["role"]: item for item in result.result["checks"]}
    assert checks["reasoning"]["status"] == "COMPLETED"
    assert checks["fast"]["status"] == "FAILED"
    assert checks["fast"]["error_type"] == "provider_blocked"
    assert checks["fast"]["request_id"] == "req-fast-blocked"
    assert "New API 前置代理/WAF" in checks["fast"]["message"]
    invocations = list(session.scalars(select(ModelInvocation).order_by(ModelInvocation.created_at)))
    assert [item.status for item in invocations] == ["COMPLETED", "FAILED"]
