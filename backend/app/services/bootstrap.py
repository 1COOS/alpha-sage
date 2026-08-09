from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import SourceHealthStatus, StrategyStatus
from app.models import (
    Account,
    CashLedgerEntry,
    MarketRuleVersion,
    SourceHealth,
    StrategyVersion,
    SystemSetting,
    utc_now,
)
from app.services.audit import append_audit, stable_hash

BASELINE_RULES = {
    "market": "CN",
    "currency": "CNY",
    "settlement": "T+1",
    "stock_lot_size": 100,
    "etf_lot_size": 100,
    "slippage_bps": 10,
    "commission_rate": "0.0003",
    "minimum_commission": "5.00",
    "stock_sell_stamp_tax_rate": "0.0005",
    "stock_transfer_fee_rate": "0.00001",
    "max_instrument_weight": "0.10",
    "max_industry_weight": "0.25",
    "max_gross_weight": "0.85",
    "drawdown_delever": "0.12",
    "drawdown_pause": "0.18",
    "horizon_budgets": {
        "SHORT": {"anchor": "0.20", "min": "0.10", "max": "0.30"},
        "SWING": {"anchor": "0.40", "min": "0.30", "max": "0.50"},
        "LONG": {"anchor": "0.40", "min": "0.30", "max": "0.50"},
    },
    "intraday": {
        "bar_minutes": 5,
        "price_trigger": "0.02",
        "volume_multiple_trigger": "3.0",
        "daily_model_call_cap": 12,
        "per_symbol_call_cap": 2,
        "cooldown_minutes": 15,
    },
}


BASELINE_STRATEGY = {
    "name": "alpha-sage-cognition-v1",
    "principles": [
        "evidence_before_opinion",
        "explicit_opposition",
        "cash_is_valid",
        "no_runtime_code_mutation",
        "manual_champion_promotion",
    ],
    "opportunity_limit": 20,
    "deep_research_limit": 8,
    "portfolio_position_min": 6,
    "portfolio_position_max": 10,
    "memory_policy": "episodic_auto_semantic_candidate_only",
}


SOURCE_REGISTRY = {
    "baostock": "daily_history_secondary",
    "eastmoney": "daily_and_intraday_primary",
    "tencent": "intraday_price_confirmation",
    "sse": "official_exchange",
    "szse": "official_exchange",
    "cninfo": "official_disclosure",
    "csrc": "official_policy",
    "pbc": "official_macro",
    "stats": "official_macro",
}


def bootstrap_system(session: Session) -> Account:
    account = session.scalar(select(Account).where(Account.name == "paper-main"))
    now = utc_now()
    if account is None:
        account = Account(
            name="paper-main",
            initial_cash=Decimal("1000000"),
            cash=Decimal("1000000"),
            high_watermark=Decimal("1000000"),
            enabled=False,
            paused_reason="等待数据、模型与日历自检通过后人工启用",
        )
        session.add(account)
        session.flush()
        session.add(
            CashLedgerEntry(
                account_id=account.id,
                event_type="INITIAL_CAPITAL",
                amount=Decimal("1000000"),
                balance_after=Decimal("1000000"),
                reference_type="ACCOUNT",
                reference_id=account.id,
                occurred_at=now,
            )
        )
        append_audit(
            session,
            event_type="ACCOUNT_CREATED",
            actor="system",
            entity_type="Account",
            entity_id=account.id,
            payload={"initial_cash": "1000000", "enabled": False},
        )

    if session.scalar(select(MarketRuleVersion).where(MarketRuleVersion.version == "cn-paper-v1")) is None:
        rules = MarketRuleVersion(
            version="cn-paper-v1",
            effective_from=date(2026, 1, 1),
            content=BASELINE_RULES,
            content_hash=stable_hash(BASELINE_RULES),
            source_uri="https://www.sse.com.cn/lawandrules/sselawsrules/trade/",
        )
        session.add(rules)
    if session.scalar(select(MarketRuleVersion).where(MarketRuleVersion.version == "cn-paper-v2")) is None:
        session.add(
            MarketRuleVersion(
                version="cn-paper-v2",
                effective_from=date(2026, 8, 6),
                content=BASELINE_RULES,
                content_hash=stable_hash(BASELINE_RULES),
                source_uri="https://www.sse.com.cn/lawandrules/sselawsrules/trade/",
            )
        )

    strategy = session.scalar(select(StrategyVersion).where(StrategyVersion.version == "alpha-sage-cognition-v1"))
    if strategy is None:
        strategy = StrategyVersion(
            version="alpha-sage-cognition-v1",
            status=StrategyStatus.CHAMPION,
            rules=BASELINE_STRATEGY,
            content_hash=stable_hash(BASELINE_STRATEGY),
            activated_at=now,
        )
        session.add(strategy)

    for source_id, role in SOURCE_REGISTRY.items():
        if session.get(SourceHealth, source_id) is None:
            session.add(
                SourceHealth(
                    source_id=source_id,
                    role=role,
                    status=SourceHealthStatus.UNAVAILABLE,
                    detail="尚未执行来源自检",
                )
            )

    defaults = {
        "trusted_media_whitelist": {
            "domains": [
                "sse.com.cn",
                "szse.cn",
                "cninfo.com.cn",
                "csrc.gov.cn",
                "pbc.gov.cn",
                "stats.gov.cn",
                "ndrc.gov.cn",
                "miit.gov.cn",
                "cs.com.cn",
                "stcn.com",
                "cnstock.com",
            ]
        },
        "runtime_policy": {
            "position_count": {"min": 6, "max": 10},
            "manual_enable_required": True,
            "runtime_code_mutation": False,
        },
        # Runtime model values come from environment defaults until the user
        # explicitly saves UI overrides. Never seed values that permanently
        # mask later .env changes.
        "model_settings": {},
    }
    for key, value in defaults.items():
        existing = session.get(SystemSetting, key)
        if existing is None:
            session.add(SystemSetting(key=key, value=value))

    session.commit()
    return account
