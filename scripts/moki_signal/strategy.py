from __future__ import annotations

import numpy as np
import pandas as pd

from .contracts import PendingSignal, SignalEvent, StrategyResult


STRATEGY_ID = "s1-reconstruction"
STRATEGY_VERSION = "1.0.0"


def rsi_wilder(close: pd.Series, period: int = 14) -> np.ndarray:
    values = np.asarray(close, dtype=float)
    delta = np.diff(values, prepend=values[0])
    up = np.where(delta > 0, delta, 0.0)
    down = np.where(delta < 0, -delta, 0.0)
    average_up = pd.Series(up).ewm(alpha=1 / period, adjust=False).mean().to_numpy()
    average_down = pd.Series(down).ewm(alpha=1 / period, adjust=False).mean().to_numpy()
    rs = average_up / np.where(average_down == 0, 1e-12, average_down)
    return 100 - 100 / (1 + rs)


def snap_rsi_pivot(rsi: np.ndarray, index: int, side: str, *, window: int = 2, available_through: int | None = None) -> int:
    lo = max(0, index - window)
    last = len(rsi) - 1 if available_through is None else available_through
    hi = min(len(rsi), index + window + 1, last + 1)
    values = rsi[lo:hi]
    return lo + int(np.argmin(values) if side == "B" else np.argmax(values))


def s1_zigzag(close: pd.Series, rsi: np.ndarray, threshold: float, pivot_window: int) -> tuple[list[SignalEvent], PendingSignal | None, int]:
    prices = np.asarray(close, dtype=float)
    if not len(prices):
        return [], None, 0
    confirmed: list[SignalEvent] = []
    direction = 0
    low_index = high_index = 0
    for index in range(1, len(prices)):
        if prices[index] < prices[low_index]:
            low_index = index
        if prices[index] > prices[high_index]:
            high_index = index
        if direction == 0:
            if prices[index] >= prices[low_index] * (1 + threshold):
                pivot = snap_rsi_pivot(rsi, low_index, "B", window=pivot_window, available_through=index)
                confirmed.append(SignalEvent(pivot, "B", index))
                direction, high_index = 1, index
            elif prices[index] <= prices[high_index] * (1 - threshold):
                pivot = snap_rsi_pivot(rsi, high_index, "S", window=pivot_window, available_through=index)
                confirmed.append(SignalEvent(pivot, "S", index))
                direction, low_index = -1, index
        elif direction == 1:
            if prices[index] > prices[high_index]:
                high_index = index
            if prices[index] <= prices[high_index] * (1 - threshold):
                pivot = snap_rsi_pivot(rsi, high_index, "S", window=pivot_window, available_through=index)
                confirmed.append(SignalEvent(pivot, "S", index))
                direction, low_index = -1, index
        else:
            if prices[index] < prices[low_index]:
                low_index = index
            if prices[index] >= prices[low_index] * (1 + threshold):
                pivot = snap_rsi_pivot(rsi, low_index, "B", window=pivot_window, available_through=index)
                confirmed.append(SignalEvent(pivot, "B", index))
                direction, high_index = 1, index
    if direction == 1:
        pending = PendingSignal(snap_rsi_pivot(rsi, high_index, "S", window=pivot_window), "S", float(prices[high_index] * (1 - threshold)))
    elif direction == -1:
        pending = PendingSignal(snap_rsi_pivot(rsi, low_index, "B", window=pivot_window), "B", float(prices[low_index] * (1 + threshold)))
    else:
        pending = None
    return confirmed, pending, direction


def run_s1_reconstruction(frame: pd.DataFrame, parameters: dict[str, float | int]) -> StrategyResult:
    rsi = rsi_wilder(frame["Close"], int(parameters["rsi_period"]))
    confirmed, pending, direction = s1_zigzag(frame["Close"], rsi, float(parameters["threshold"]), int(parameters["pivot_window"]))
    return StrategyResult(STRATEGY_ID, STRATEGY_VERSION, parameters, confirmed, pending, direction)


STRATEGIES = {STRATEGY_ID: run_s1_reconstruction}


def run_strategy(strategy_id: str, frame: pd.DataFrame, parameters: dict[str, float | int]) -> StrategyResult:
    if strategy_id not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy_id}")
    return STRATEGIES[strategy_id](frame, parameters)
