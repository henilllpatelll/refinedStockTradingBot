from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

import pandas as pd


def ema_series(values: Iterable[float], period: int) -> list[Optional[float]]:
    """Return EMA values seeded by the SMA of the first full period."""
    if period <= 0:
        raise ValueError("period must be positive")

    result: list[Optional[float]] = []
    acc = 0.0
    ema: Optional[float] = None
    k = 2.0 / (period + 1)

    for index, raw in enumerate(values, start=1):
        price = float(raw)
        if index < period:
            acc += price
            result.append(None)
            continue
        if index == period:
            acc += price
            ema = acc / period
            result.append(ema)
            continue
        assert ema is not None
        ema = ema + k * (price - ema)
        result.append(ema)

    return result


def ema_last(values: Iterable[float], period: int) -> Optional[float]:
    series = ema_series(values, period)
    return series[-1] if series else None


def atr(bars: pd.DataFrame, period: int = 14) -> Optional[float]:
    """Compute simple ATR over the last `period` true ranges."""
    if len(bars) < period:
        return None

    true_ranges: list[float] = []
    previous_close: Optional[float] = None
    for row in bars[["high", "low", "close"]].itertuples(index=False):
        high = float(row.high)
        low = float(row.low)
        close = float(row.close)
        if previous_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(tr)
        previous_close = close

    return sum(true_ranges[-period:]) / period


def fifty_two_week_high(bars: pd.DataFrame) -> Optional[float]:
    if bars.empty or "high" not in bars:
        return None
    window = bars.tail(252)
    return float(window["high"].max())


def volume_sma(bars: pd.DataFrame, period: int = 20) -> Optional[float]:
    if len(bars) < period or "volume" not in bars:
        return None
    return float(bars["volume"].tail(period).mean())


def stop_price_from_atr(entry_price: float, atr_value: float) -> float:
    return float(entry_price) - 1.5 * float(atr_value)
