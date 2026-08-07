from enum import StrEnum


class Horizon(StrEnum):
    SHORT = "SHORT"
    SWING = "SWING"
    LONG = "LONG"


class RunKind(StrEnum):
    EOD = "EOD"
    INTRADAY = "INTRADAY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    DATA_SYNC = "DATA_SYNC"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class DecisionAction(StrEnum):
    BUY = "BUY"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    SELL = "SELL"
    WATCH = "WATCH"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class StrategyStatus(StrEnum):
    CHAMPION = "CHAMPION"
    SUPERSEDED = "SUPERSEDED"
    CHALLENGER = "CHALLENGER"


class ChallengerStatus(StrEnum):
    DRAFT = "DRAFT"
    BLOCKED_INSUFFICIENT_EVIDENCE = "BLOCKED_INSUFFICIENT_EVIDENCE"
    SHADOW = "SHADOW"
    ELIGIBLE = "ELIGIBLE"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class SourceHealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
