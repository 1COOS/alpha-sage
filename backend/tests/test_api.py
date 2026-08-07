import httpx
from fastapi import FastAPI
from sqlalchemy import text

from app.api.routes import router
from app.db import get_session


async def test_status_and_paused_agent_routes_do_not_require_model_key(session):
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
        assert eod.status_code == 200
        assert eod.json()["status"] == "BLOCKED"
        assert "尚未人工启用" in eod.json()["blocker"]

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
