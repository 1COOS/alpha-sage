from app.domain.enums import RunKind
from app.services import scheduler


def test_scheduler_submits_attribution_with_scheduler_source(monkeypatch):
    captured = {}

    class FakeQueue:
        @staticmethod
        def submit(**kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(scheduler, "RUN_QUEUE", FakeQueue())

    scheduler._attribute()

    assert captured["kind"] == RunKind.ATTRIBUTION
    assert captured["trigger_source"] == "SCHEDULER"
    assert captured["parameters"]["as_of"]


def test_scheduler_intraday_requests_busy_skip_without_delayed_replay(monkeypatch):
    captured = {}

    class FakeQueue:
        @staticmethod
        def submit(**kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(scheduler, "RUN_QUEUE", FakeQueue())
    monkeypatch.setattr(scheduler, "_account_can_execute", lambda _session: True)

    scheduler._run_intraday()

    assert captured["kind"] == RunKind.INTRADAY
    assert captured["trigger_source"] == "SCHEDULER"
    assert captured["reject_duplicate"] is False
    assert captured["skip_if_busy"] is True
