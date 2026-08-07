from __future__ import annotations

import asyncio
from datetime import date
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.domain.enums import OrderStatus
from app.models import Account, OrderPlan
from app.services.agent import CognitiveAgent
from app.services.evolution import EvolutionService, ExperienceService
from app.services.intraday import IntradayService

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _account_enabled(session) -> bool:
    account = session.scalar(select(Account).where(Account.name == "paper-main"))
    return bool(account and account.enabled)


def _account_can_execute(session) -> bool:
    account = session.scalar(select(Account).where(Account.name == "paper-main"))
    if account is None:
        return False
    if account.enabled:
        return True
    if not (account.paused_reason or "").startswith("组合回撤达到18%"):
        return False
    pending_sell = session.scalar(
        select(OrderPlan.id)
        .where(
            OrderPlan.account_id == account.id,
            OrderPlan.side == "SELL",
            OrderPlan.status.in_([OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED]),
        )
        .limit(1)
    )
    return pending_sell is not None


def _run_eod() -> None:
    with SessionLocal() as session:
        if not _account_enabled(session):
            return
        CognitiveAgent(session).run_eod(date.today())


def _run_intraday() -> None:
    with SessionLocal() as session:
        if not _account_can_execute(session):
            return
        asyncio.run(IntradayService(session).run())


def _attribute() -> None:
    with SessionLocal() as session:
        ExperienceService(session).attribute_due()


def _weekly() -> None:
    with SessionLocal() as session:
        if not _account_enabled(session):
            return
        EvolutionService(session).generate_weekly_lessons()


def _monthly() -> None:
    with SessionLocal() as session:
        if not _account_enabled(session):
            return
        EvolutionService(session).generate_monthly_challenger()


def create_scheduler() -> BackgroundScheduler | None:
    if not get_settings().scheduler_enabled:
        return None
    scheduler = BackgroundScheduler(
        timezone=SHANGHAI,
        job_defaults={"coalesce": False, "misfire_grace_time": 60, "max_instances": 1},
    )
    scheduler.add_job(_run_eod, CronTrigger(day_of_week="mon-fri", hour=15, minute=20), id="eod")
    scheduler.add_job(
        _run_intraday,
        CronTrigger(day_of_week="mon-fri", hour=9, minute="30-59/5"),
        id="intraday-morning-1",
    )
    scheduler.add_job(
        _run_intraday,
        CronTrigger(day_of_week="mon-fri", hour=10, minute="*/5"),
        id="intraday-morning-1b",
    )
    scheduler.add_job(
        _run_intraday,
        CronTrigger(day_of_week="mon-fri", hour=11, minute="0-30/5"),
        id="intraday-morning-2",
    )
    scheduler.add_job(
        _run_intraday,
        CronTrigger(day_of_week="mon-fri", hour="13-14", minute="*/5"),
        id="intraday-afternoon",
    )
    scheduler.add_job(_attribute, CronTrigger(day_of_week="mon-fri", hour=15, minute=40), id="attribution")
    scheduler.add_job(_weekly, CronTrigger(day_of_week="fri", hour=18, minute=0), id="weekly")
    scheduler.add_job(_monthly, CronTrigger(day="1-7", day_of_week="fri", hour=19, minute=0), id="monthly")
    return scheduler
