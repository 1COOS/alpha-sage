import httpx
from fastapi import FastAPI
from sqlalchemy import text

from app.api import routes
from app.api.routes import router
from app.db import get_session
from app.domain.enums import RunStatus
from app.models import AgentRun


async def test_status_and_agent_submission_do_not_require_model_key(session, monkeypatch):
    class FakeQueue:
        def submit(self, **kwargs):
            return AgentRun(
                id="run_test",
                kind=kwargs["kind"],
                status=RunStatus.PENDING,
                trigger_source=kwargs["trigger_source"],
                parameters=kwargs["parameters"],
                stage="QUEUED",
                progress_message="等待前序任务完成",
            )

    monkeypatch.setattr(routes, "RUN_QUEUE", FakeQueue())
    test_app = FastAPI()
    test_app.include_router(router)

    def override_session():
        yield session

    test_app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        status = await client.get("/api/v1/system/status")
        assert status.status_code == 200
        assert status.json()["account_enabled"] is False

        eod = await client.post("/api/v1/agent/eod")
        assert eod.status_code == 202
        assert eod.json()["status"] == "PENDING"
        assert eod.json()["kind"] == "EOD"

        enable = await client.post(
            "/api/v1/system/enable",
            json={"confirmation": "ENABLE PAPER ACCOUNT"},
        )
        assert enable.status_code == 409
        assert enable.json()["detail"]["passed"] is False


async def test_experience_search_uses_fts5_index(session):
    session.execute(
        text(
            "CREATE VIRTUAL TABLE experiences_fts USING fts5("
            "thesis_summary, tags, market_regime, content='experiences', content_rowid='rowid', "
            "tokenize='trigram')"
        )
    )
    session.execute(
        text(
            "CREATE TRIGGER experiences_fts_insert AFTER INSERT ON experiences BEGIN "
            "INSERT INTO experiences_fts(rowid, thesis_summary, tags, market_regime) "
            "VALUES (new.rowid, new.thesis_summary, new.tags, new.market_regime); END"
        )
    )
    session.execute(
        text(
            "INSERT INTO experiences ("
            "id,decision_id,strategy_version_id,instrument_id,horizon,market_regime,event_types,"
            "thesis_summary,outcome_date,net_return,benchmark_return,excess_return,direction_hit,"
            "brier_score,max_favorable_excursion,max_adverse_excursion,attribution,tags,created_at"
            ") VALUES ("
            "'exp_fts','dec_fts','strategy_fts','instrument_fts','LONG','BEAR','[]',"
            "'产业库存反转形成可证伪机会','2026-08-06',0.1,0.02,0.08,1,0.1,0.12,-0.03,'{}',"
            "'[\"LONG\",\"BUY\"]','2026-08-06T08:00:00+00:00')"
        )
    )
    session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_session():
        yield session

    test_app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/experiences/search", params={"q": "库存 反转"})

    assert response.status_code == 200
    assert response.json()[0]["id"] == "exp_fts"


async def test_model_connection_test_queues_current_form_without_persisting_key(monkeypatch):
    captured = {}

    class FakeQueue:
        def submit(self, **kwargs):
            captured.update(kwargs)
            return AgentRun(
                id="run_model_test",
                kind=kwargs["kind"],
                status=RunStatus.PENDING,
                trigger_source=kwargs["trigger_source"],
                parameters=kwargs["parameters"],
                stage="QUEUED",
            )

    monkeypatch.setattr(routes, "RUN_QUEUE", FakeQueue())
    test_app = FastAPI()
    test_app.include_router(router)
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/settings/model/test",
            json={
                "base_url": "https://provider.example/v1",
                "api_mode": "responses",
                "reasoning_model": "reasoning-current-form",
                "fast_model": "fast-current-form",
                "daily_request_budget": 100,
                "api_key": "temporary-key",
            },
        )

    assert response.status_code == 202
    assert response.json()["run_id"] == "run_model_test"
    assert captured["parameters"]["reasoning_model"] == "reasoning-current-form"
    assert captured["parameters"]["api_key_supplied"] is True
    assert "api_key" not in captured["parameters"]
