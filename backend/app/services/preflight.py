from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import SourceHealthStatus, StrategyStatus
from app.domain.schemas import PreflightCheck, PreflightReport
from app.models import (
    Account,
    DataArtifact,
    Instrument,
    MarketCalendarDay,
    SourceHealth,
    StrategyVersion,
)
from app.services.market_adapter import CNMarketAdapter
from app.services.secrets import SecretStore
from app.temporal import beijing_today


class PreflightService:
    def __init__(self, session: Session, model_ready: bool | None = None):
        self.session = session
        self.model_ready = model_ready

    def run(self) -> PreflightReport:
        checks: list[PreflightCheck] = []
        account = self.session.scalar(select(Account).where(Account.name == "paper-main"))
        checks.append(
            PreflightCheck(
                key="account",
                passed=account is not None,
                detail="模拟账户已初始化" if account else "请先执行 init-db",
            )
        )

        try:
            rule = CNMarketAdapter(self.session).rule_version(beijing_today())
            required = {
                "commission_rate",
                "minimum_commission",
                "stock_sell_stamp_tax_rate",
                "stock_transfer_fee_rate",
            }
            missing = required - set(rule.content)
            checks.append(
                PreflightCheck(
                    key="market_rules",
                    passed=not missing,
                    detail=(
                        f"当前A股规则版本：{rule.version}"
                        if not missing
                        else f"交易规则版本 {rule.version} 缺少字段：{', '.join(sorted(missing))}"
                    ),
                )
            )
        except RuntimeError as exc:
            checks.append(PreflightCheck(key="market_rules", passed=False, detail=str(exc)))

        champion = self.session.scalar(select(StrategyVersion).where(StrategyVersion.status == StrategyStatus.CHAMPION))
        checks.append(
            PreflightCheck(
                key="strategy",
                passed=champion is not None,
                detail=champion.version if champion else "缺少冠军策略",
            )
        )

        investable_count = (
            self.session.scalar(select(func.count()).select_from(Instrument).where(Instrument.investable.is_(True)))
            or 0
        )
        checks.append(
            PreflightCheck(
                key="universe",
                passed=investable_count >= 6,
                detail=f"精选池已有 {investable_count} 个可投资标的；至少需要6个",
            )
        )

        investable = {
            item.symbol: item
            for item in self.session.scalars(select(Instrument).where(Instrument.investable.is_(True)))
        }
        today = beijing_today()
        history_target = today - timedelta(days=5 * 366)
        covered_symbols: set[str] = set()
        artifacts = self.session.scalars(
            select(DataArtifact)
            .where(DataArtifact.dataset == "daily_bar_confirmed")
            .order_by(DataArtifact.sealed_at.desc())
        )
        for artifact in artifacts:
            symbol = artifact.metadata_json.get("symbol")
            instrument = investable.get(symbol)
            if instrument is None or artifact.min_date is None or artifact.max_date is None:
                continue
            expected_start = max(history_target, instrument.listed_on) if instrument.listed_on else history_target
            if artifact.min_date <= expected_start + timedelta(days=45) and artifact.max_date >= today - timedelta(
                days=10
            ):
                covered_symbols.add(symbol)
        checks.append(
            PreflightCheck(
                key="history",
                passed=len(covered_symbols) >= 6,
                detail=f"已有 {len(covered_symbols)} 个可投资标的具备足期双源确认历史；至少需要6个",
            )
        )

        start = today - timedelta(days=10)
        calendar_count = (
            self.session.scalar(
                select(func.count()).select_from(MarketCalendarDay).where(MarketCalendarDay.trade_date >= start)
            )
            or 0
        )
        checks.append(
            PreflightCheck(
                key="calendar",
                passed=calendar_count > 0,
                detail="近期交易日历可用" if calendar_count else "缺少近期交易日历",
            )
        )

        model_ready = self.model_ready if self.model_ready is not None else SecretStore.is_configured()
        checks.append(
            PreflightCheck(
                key="model",
                passed=model_ready,
                detail="模型密钥已配置" if model_ready else "请通过设置页或 OPENAI_API_KEY 配置密钥",
            )
        )

        critical_sources = list(
            self.session.scalars(
                select(SourceHealth).where(SourceHealth.source_id.in_(["eastmoney", "baostock", "tencent"]))
            )
        )
        healthy_sources = {row.source_id for row in critical_sources if row.status == SourceHealthStatus.HEALTHY}
        historical_pair_ready = "baostock" in healthy_sources and bool(
            healthy_sources.intersection({"eastmoney", "tencent"})
        )
        checks.append(
            PreflightCheck(
                key="sources",
                passed=historical_pair_ready,
                detail=(
                    f"关键历史来源健康：{', '.join(sorted(healthy_sources)) or '无'}；"
                    "要求 BaoStock + Eastmoney/腾讯至少一项"
                ),
            )
        )
        return PreflightReport(passed=all(check.passed for check in checks), checks=checks)
