from decimal import Decimal

from sqlalchemy import select

from app.domain.enums import StrategyStatus
from app.models import Account, CashLedgerEntry, StrategyVersion


def test_bootstrap_creates_paused_account_and_champion(session):
    account = session.scalar(select(Account).where(Account.name == "paper-main"))
    champion = session.scalar(select(StrategyVersion).where(StrategyVersion.status == StrategyStatus.CHAMPION))
    cash_entry = session.scalar(select(CashLedgerEntry))

    assert account is not None
    assert account.enabled is False
    assert account.cash == Decimal("1000000")
    assert champion is not None
    assert champion.version == "alpha-sage-cognition-v1"
    assert cash_entry is not None
    assert cash_entry.balance_after == Decimal("1000000")
