from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.domain.enums import RunKind, RunStatus
from app.models import AgentRun, ModelInvocation, SystemSetting
from app.services.bootstrap import bootstrap_system
from app.services.model import OpenAICompatibleModel
from app.services.run_queue import ActiveRunConflict, LocalRunQueue


def _factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        bootstrap_system(session)
    return factory


def test_local_run_queue_executes_long_tasks_serially():
    factory = _factory()
    queue = LocalRunQueue(factory)
    order: list[str] = []
    finished = Event()

    def first(_session, _run, reporter):
        order.append("first-start")
        order.append("first-end")
        return reporter.complete({"order": 1})

    def second(_session, _run, reporter):
        order.append("second-start")
        order.append("second-end")
        finished.set()
        return reporter.complete({"order": 2})

    queue.submit(kind=RunKind.EOD, trigger_source="MANUAL", parameters={}, job=first)
    queue.submit(kind=RunKind.WEEKLY, trigger_source="MANUAL", parameters={}, job=second)

    assert finished.wait(2)
    assert order == ["first-start", "first-end", "second-start", "second-end"]
    queue.shutdown()


def test_local_run_queue_rejects_duplicate_active_kind_and_skips_intraday_when_busy():
    factory = _factory()
    queue = LocalRunQueue(factory)
    release = Event()
    started = Event()

    def blocking(_session, _run, reporter):
        started.set()
        release.wait(2)
        return reporter.complete()

    active = queue.submit(kind=RunKind.EOD, trigger_source="MANUAL", parameters={}, job=blocking)
    assert started.wait(1)
    with pytest.raises(ActiveRunConflict) as error:
        queue.submit(kind=RunKind.EOD, trigger_source="MANUAL", parameters={}, job=blocking)
    assert error.value.run.id == active.id

    skipped = queue.submit(
        kind=RunKind.INTRADAY,
        trigger_source="SCHEDULER",
        parameters={},
        job=blocking,
        reject_duplicate=False,
        skip_if_busy=True,
    )
    assert skipped.status == RunStatus.SKIPPED
    assert "不延迟补跑" in skipped.blocker
    release.set()
    queue.shutdown()


def test_recover_stale_runs_marks_them_failed_without_replaying():
    factory = _factory()
    with factory() as session:
        session.add_all(
            [
                AgentRun(kind=RunKind.EOD, status=RunStatus.PENDING),
                AgentRun(kind=RunKind.DATA_SYNC, status=RunStatus.RUNNING),
            ]
        )
        session.commit()

    queue = LocalRunQueue(factory)
    assert queue.recover_stale_runs() == 2
    with factory() as session:
        rows = list(session.scalars(select(AgentRun).order_by(AgentRun.started_at)))
        stale = [row for row in rows if row.kind in {RunKind.EOD, RunKind.DATA_SYNC}]
        assert {row.status for row in stale} == {RunStatus.FAILED}
        assert all("不会自动重放" in (row.blocker or "") for row in stale)
    queue.shutdown()


def test_queue_rolls_back_partial_work_but_restores_failed_model_audit():
    factory = _factory()
    queue = LocalRunQueue(factory)

    class BlockedError(RuntimeError):
        status_code = 403

    def job(session, run, reporter):
        reporter.update("GENERATE_LESSONS", "调用模型生成周度规律")
        session.add(SystemSetting(key="partial-weekly-output", value={"must": "rollback"}))
        model = OpenAICompatibleModel(
            session,
            candidate={
                "base_url": "https://provider.example/v1",
                "api_mode": "responses",
                "reasoning_model": "reasoning-test",
                "fast_model": "fast-test",
                "daily_request_budget": 100,
            },
            api_key_override="temporary-test-key",
            timeout_seconds=1,
            max_retries=0,
        )

        def raise_blocked(**_kwargs):
            raise BlockedError("Your request was blocked.")

        model.client = SimpleNamespace(responses=SimpleNamespace(create=raise_blocked))
        model.complete_text(
            purpose="weekly-lessons",
            system="system",
            user="user",
            run_id=run.id,
        )

    submitted = queue.submit(
        kind=RunKind.WEEKLY,
        trigger_source="MANUAL",
        parameters={},
        job=job,
    )
    deadline = monotonic() + 2
    failed = None
    while monotonic() < deadline:
        with factory() as session:
            failed = session.get(AgentRun, submitted.id)
            if failed and failed.status == RunStatus.FAILED:
                break
        sleep(0.01)

    assert failed is not None
    assert failed.status == RunStatus.FAILED
    assert failed.stage == "GENERATE_LESSONS"
    assert failed.result["failure"]["model"] == "reasoning-test"
    with factory() as session:
        assert session.get(SystemSetting, "partial-weekly-output") is None
        invocation = session.scalar(select(ModelInvocation).where(ModelInvocation.run_id == submitted.id))
        assert invocation is not None
        assert invocation.status == "FAILED"
        assert invocation.error_type == "provider_blocked"
    queue.shutdown()
