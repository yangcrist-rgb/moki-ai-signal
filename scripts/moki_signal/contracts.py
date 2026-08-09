from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Instrument:
    input_ticker: str
    ticker: str
    company_name: str
    currency: str
    exchange: str


@dataclass(frozen=True)
class SignalEvent:
    pivot_index: int
    side: str
    confirmation_index: int


@dataclass(frozen=True)
class PendingSignal:
    pivot_index: int
    side: str
    trigger_close: float


@dataclass(frozen=True)
class StrategyResult:
    strategy_id: str
    strategy_version: str
    parameters: dict[str, Any]
    confirmed: list[SignalEvent]
    pending: PendingSignal | None
    direction: int


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value
