from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Lock
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db import SessionLocal
from app.domain.enums import RunStatus
from app.models import AgentRun, utc_now

ACTIVE_RUN_STATUSES = (RunStatus.PENDING, RunStatus.RUNNING)
TERMINAL_RUN_STATUSES = (
    RunStatus.COMPLETED,
    RunStatus.BLOCKED,
    RunStatus.FAILED,
    RunStatus.SKIPPED,
)

RunJob = Callable[[Session, AgentRun, "RunProgressReporter"], AgentRun | dict[str, Any] | None]


class ActiveRunConflict(RuntimeError):
    def __init__(self, run: AgentRun):
        super().__init__(f"已有同类任务正在执行：{run.id}")
        self.run = run


class RunProgressReporter:
    def __init__(self, session: Session, run: AgentRun):
        self.session = session
        self.run = run

    def update(
        self,
        stage: str,
        message: str,
        *,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        self.run.stage = stage
        self.run.progress_message = message
        self.run.progress_current = current
        self.run.progress_total = total
        self.run.updated_at = utc_now()
        self.session.commit()

    def complete(self, result: dict[str, Any] | None = None, message: str = "任务完成") -> AgentRun:
        self.run.status = RunStatus.COMPLETED
        self.run.stage = "COMPLETED"
        self.run.progress_message = message
        if result is not None:
            self.run.result = result
        self.run.finished_at = utc_now()
        self.run.updated_at = self.run.finished_at
        self.session.commit()
        return self.run

    def block(self, reason: str) -> AgentRun:
        self.run.status = RunStatus.BLOCKED
        self.run.stage = "BLOCKED"
        self.run.progress_message = reason
        self.run.blocker = reason
        self.run.finished_at = utc_now()
        self.run.updated_at = self.run.finished_at
        self.session.commit()
        return self.run

    def fail(
        self,
        reason: str,
        *,
        stage: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> AgentRun:
        self.run.status = RunStatus.FAILED
        self.run.stage = stage or "FAILED"
        self.run.progress_message = reason
        self.run.blocker = reason
        if result is not None:
            self.run.result = result
        self.run.finished_at = utc_now()
        self.run.updated_at = self.run.finished_at
        self.session.commit()
        return self.run


class LocalRunQueue:
    def __init__(self, session_factory: sessionmaker[Session] = SessionLocal) -> None:
        self.session_factory = session_factory
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alpha-sage-run")
        self._submit_lock = Lock()
        self._closed = False

    def recover_stale_runs(self) -> int:
        with self.session_factory() as session:
            rows = list(session.scalars(select(AgentRun).where(AgentRun.status.in_(ACTIVE_RUN_STATUSES))))
            now = utc_now()
            for run in rows:
                run.status = RunStatus.FAILED
                run.stage = "FAILED"
                run.blocker = "服务已重启，未完成任务不会自动重放，请人工确认后重新运行"
                run.progress_message = run.blocker
                run.finished_at = now
                run.updated_at = now
            session.commit()
            return len(rows)

    def submit(
        self,
        *,
        kind: str,
        trigger_source: str,
        parameters: dict[str, Any] | None,
        job: RunJob,
        trade_date: date | None = None,
        reject_duplicate: bool = True,
        skip_if_busy: bool = False,
    ) -> AgentRun:
        with self._submit_lock, self.session_factory() as session:
            if self._closed:
                raise RuntimeError("本地任务队列已停止")
            if reject_duplicate:
                duplicate = session.scalar(
                    select(AgentRun)
                    .where(AgentRun.kind == kind, AgentRun.status.in_(ACTIVE_RUN_STATUSES))
                    .order_by(AgentRun.started_at)
                    .limit(1)
                )
                if duplicate is not None:
                    raise ActiveRunConflict(duplicate)
            active = session.scalar(
                select(AgentRun).where(AgentRun.status.in_(ACTIVE_RUN_STATUSES)).order_by(AgentRun.started_at).limit(1)
            )
            if skip_if_busy and active is not None:
                now = utc_now()
                run = AgentRun(
                    kind=kind,
                    status=RunStatus.SKIPPED,
                    trigger_source=trigger_source,
                    parameters=parameters or {},
                    trade_date=trade_date,
                    stage="SKIPPED",
                    progress_message=f"前序任务 {active.id} 尚未完成，本次高频任务不延迟补跑",
                    blocker=f"前序任务 {active.id} 尚未完成，本次高频任务不延迟补跑",
                    finished_at=now,
                    updated_at=now,
                )
                session.add(run)
                session.commit()
                return run
            run = AgentRun(
                kind=kind,
                status=RunStatus.PENDING,
                trigger_source=trigger_source,
                parameters=parameters or {},
                trade_date=trade_date,
                stage="QUEUED",
                progress_message="等待前序任务完成",
                updated_at=utc_now(),
            )
            session.add(run)
            session.commit()
            run_id = run.id
        self._executor.submit(self._execute, run_id, job)
        return run

    def _execute(self, run_id: str, job: RunJob) -> None:
        with self.session_factory() as session:
            run = session.get(AgentRun, run_id)
            if run is None or run.status != RunStatus.PENDING:
                return
            run.status = RunStatus.RUNNING
            run.stage = "STARTING"
            run.progress_message = "任务开始执行"
            run.updated_at = utc_now()
            session.commit()
            reporter = RunProgressReporter(session, run)
            try:
                result = job(session, run, reporter)
                session.refresh(run)
                if run.status in TERMINAL_RUN_STATUSES:
                    return
                if isinstance(result, AgentRun):
                    session.refresh(result)
                    if result.status in TERMINAL_RUN_STATUSES:
                        return
                    reporter.run = result
                reporter.complete(result if isinstance(result, dict) else None)
            except Exception as exc:  # noqa: BLE001 - every background failure must become visible
                failed_stage = run.stage
                session.rollback()
                run = session.get(AgentRun, run_id)
                if run is not None:
                    from app.services.model import format_run_failure, restore_failed_model_invocation

                    restore_failed_model_invocation(session, exc)
                    message, result = format_run_failure(
                        session,
                        run_id=run.id,
                        stage=failed_stage,
                        exc=exc,
                    )
                    RunProgressReporter(session, run).fail(
                        message,
                        stage=failed_stage,
                        result=result,
                    )

    def shutdown(self) -> None:
        with self._submit_lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)


RUN_QUEUE = LocalRunQueue()
