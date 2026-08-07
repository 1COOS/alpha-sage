from __future__ import annotations

import asyncio
from datetime import date

import typer
from alembic.config import Config

from alembic import command
from app.config import BACKEND_ROOT
from app.db import SessionLocal
from app.services.agent import CognitiveAgent
from app.services.bootstrap import bootstrap_system
from app.services.data_sync import HistorySyncService
from app.services.evolution import EvolutionService, ExperienceService
from app.services.intraday import IntradayService
from app.services.preflight import PreflightService

app = typer.Typer(no_args_is_help=True)


@app.command("migrate")
def migrate() -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(config, "head")


@app.command("bootstrap")
def bootstrap() -> None:
    with SessionLocal() as session:
        account = bootstrap_system(session)
        typer.echo(f"initialized {account.name}; enabled={account.enabled}")


@app.command("preflight")
def preflight() -> None:
    with SessionLocal() as session:
        report = PreflightService(session).run()
        for check in report.checks:
            typer.echo(f"{'PASS' if check.passed else 'BLOCK'} {check.key}: {check.detail}")
        raise typer.Exit(0 if report.passed else 2)


@app.command("sync-history")
def sync_history(years: int = 5, limit: int | None = None) -> None:
    with SessionLocal() as session:
        run = asyncio.run(HistorySyncService(session).sync(years=years, limit=limit))
        typer.echo(f"{run.status}: {run.result or run.blocker}")
        raise typer.Exit(0 if run.status == "COMPLETED" else 2)


@app.command("run-eod")
def run_eod(trade_date: str | None = None) -> None:
    with SessionLocal() as session:
        resolved = date.fromisoformat(trade_date) if trade_date else date.today()
        run = CognitiveAgent(session).run_eod(resolved)
        typer.echo(f"{run.status}: {run.result or run.blocker}")


@app.command("run-intraday")
def run_intraday(trade_date: str | None = None) -> None:
    with SessionLocal() as session:
        resolved = date.fromisoformat(trade_date) if trade_date else None
        run = asyncio.run(IntradayService(session).run(resolved))
        typer.echo(f"{run.status}: {run.result or run.blocker}")


@app.command("attribute")
def attribute(as_of: str | None = None) -> None:
    with SessionLocal() as session:
        resolved = date.fromisoformat(as_of) if as_of else None
        rows = ExperienceService(session).attribute_due(resolved)
        typer.echo(f"created {len(rows)} experiences")


@app.command("weekly")
def weekly() -> None:
    with SessionLocal() as session:
        run = EvolutionService(session).generate_weekly_lessons()
        typer.echo(f"{run.status}: {run.result or run.blocker}")


@app.command("monthly")
def monthly() -> None:
    with SessionLocal() as session:
        run = EvolutionService(session).generate_monthly_challenger()
        typer.echo(f"{run.status}: {run.result or run.blocker}")
