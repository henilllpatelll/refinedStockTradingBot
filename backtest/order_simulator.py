from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass
class SimulatedFill:
    symbol: str
    strategy_id: str
    entry_date: date
    entry_price: float
    shares: int
    catalyst_type: str
    atr_at_entry: float
    exit_date: date | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    realized_pnl: float = 0.0
    t1_filled: bool = False

    @property
    def hold_days(self) -> int:
        if self.exit_date is None or self.entry_date is None:
            return 0
        return (self.exit_date - self.entry_date).days


def simulate_fill(
    symbol: str,
    strategy_id: str,
    signal_date: date,
    bars: pd.DataFrame,
    *,
    slippage_pct: float = 0.001,
    t1_target_pct: float = 0.03,
    trail_stop_pct: float = 0.025,
    atr_stop_mult: float = 1.5,
    max_hold_days: int = 14,
    catalyst_type: str = "technical_breakout",
) -> SimulatedFill | None:
    """Simulate entry on signal_date's close and track exit on subsequent bars."""
    if bars.empty or "close" not in bars:
        return None

    if hasattr(bars.index, "date"):
        dates = [d.date() if hasattr(d, "date") else d for d in bars.index]
    else:
        dates = list(bars.index)

    try:
        signal_idx = dates.index(signal_date)
    except ValueError:
        return None

    entry_bar = bars.iloc[signal_idx]
    entry_price = float(entry_bar["close"]) * (1 + slippage_pct)
    shares = max(1, int(500 // entry_price))

    atr_window = bars.iloc[max(0, signal_idx - 14): signal_idx + 1]
    atr_val = _simple_atr(atr_window) or (entry_price * 0.02)
    stop_price = entry_price - atr_stop_mult * atr_val

    fill = SimulatedFill(
        symbol=symbol,
        strategy_id=strategy_id,
        entry_date=signal_date,
        entry_price=entry_price,
        shares=shares,
        catalyst_type=catalyst_type,
        atr_at_entry=atr_val,
    )

    highest = entry_price
    t1_price = entry_price * (1 + t1_target_pct)
    remaining = shares

    for bar_idx in range(signal_idx + 1, len(bars)):
        bar = bars.iloc[bar_idx]
        bar_date = dates[bar_idx]
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        highest = max(highest, high)

        hold = (bar_date - signal_date).days if isinstance(bar_date, date) else bar_idx - signal_idx

        if low <= stop_price:
            fill.exit_date = bar_date
            fill.exit_price = stop_price * (1 - slippage_pct)
            fill.exit_reason = "STOP_LOSS"
            fill.realized_pnl = round((fill.exit_price - entry_price) * remaining, 2)
            return fill

        if not fill.t1_filled and high >= t1_price:
            t1_shares = max(1, shares // 2)
            fill.realized_pnl += (t1_price - entry_price) * t1_shares
            remaining -= t1_shares
            fill.t1_filled = True
            stop_price = entry_price  # move stop to breakeven

        if fill.t1_filled and remaining > 0:
            runner_stop = max(entry_price, highest * (1 - trail_stop_pct))
            if low <= runner_stop:
                fill.exit_date = bar_date
                fill.exit_price = runner_stop * (1 - slippage_pct)
                fill.exit_reason = "TRAILING_STOP"
                fill.realized_pnl += round((fill.exit_price - entry_price) * remaining, 2)
                fill.realized_pnl = round(fill.realized_pnl, 2)
                return fill

        if hold >= max_hold_days:
            fill.exit_date = bar_date
            fill.exit_price = close
            fill.exit_reason = "MAX_HOLD_DAYS"
            fill.realized_pnl += round((close - entry_price) * remaining, 2)
            fill.realized_pnl = round(fill.realized_pnl, 2)
            return fill

    last_bar = bars.iloc[-1]
    fill.exit_date = dates[-1]
    fill.exit_price = float(last_bar["close"])
    fill.exit_reason = "END_OF_DATA"
    fill.realized_pnl += round((fill.exit_price - entry_price) * remaining, 2)
    fill.realized_pnl = round(fill.realized_pnl, 2)
    return fill


def _simple_atr(bars: pd.DataFrame, period: int = 14) -> float | None:
    if len(bars) < 2:
        return None
    trs = []
    prev_close = None
    for row in bars[["high", "low", "close"]].itertuples(index=False):
        h, l, c = float(row.high), float(row.low), float(row.close)
        tr = h - l if prev_close is None else max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    tail = trs[-period:] if len(trs) >= period else trs
    return sum(tail) / len(tail) if tail else None
