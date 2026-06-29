from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backtest.order_simulator import SimulatedFill


@dataclass
class BacktestStats:
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    avg_win: float
    avg_loss: float
    rr_ratio: float
    max_drawdown: float
    sharpe_ratio: float
    sortino_ratio: float
    max_win_streak: int
    max_loss_streak: int
    avg_hold_days: float
    expectancy: float

    def summary(self) -> str:
        return (
            f"Trades={self.total_trades} Win%={self.win_rate:.1f}% "
            f"PnL={self.total_pnl:.2f} RR={self.rr_ratio:.2f} "
            f"MaxDD={self.max_drawdown:.2f} Sharpe={self.sharpe_ratio:.2f} "
            f"Expectancy={self.expectancy:.2f}/trade"
        )


def compute_stats(fills: list) -> BacktestStats:
    """Compute performance stats from a list of SimulatedFill objects."""
    if not fills:
        return BacktestStats(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0, 0.0)

    pnls = [f.realized_pnl for f in fills]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_pnl = round(sum(pnls), 2)
    avg_win = round(sum(wins) / len(wins), 2) if wins else 0.0
    avg_loss = round(abs(sum(losses) / len(losses)), 2) if losses else 0.0
    rr = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0
    win_rate = round(len(wins) / len(pnls) * 100, 1)
    expectancy = round(total_pnl / len(pnls), 2)

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)

    mean_pnl = sum(pnls) / len(pnls)
    variance = sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls)
    std_pnl = math.sqrt(variance) if variance > 0 else 1.0
    downside = [min(0, p - mean_pnl) ** 2 for p in pnls]
    downside_std = math.sqrt(sum(downside) / len(downside)) if downside else 1.0
    sharpe = round(mean_pnl / std_pnl * math.sqrt(252), 2) if std_pnl > 0 else 0.0
    sortino = round(mean_pnl / downside_std * math.sqrt(252), 2) if downside_std > 0 else 0.0

    max_w = max_l = cur_w = cur_l = 0
    for p in pnls:
        if p > 0:
            cur_w += 1; cur_l = 0; max_w = max(max_w, cur_w)
        else:
            cur_l += 1; cur_w = 0; max_l = max(max_l, cur_l)

    avg_hold = round(sum(f.hold_days for f in fills) / len(fills), 1)

    return BacktestStats(
        total_trades=len(fills), wins=len(wins), losses=len(losses),
        win_rate=win_rate, total_pnl=total_pnl, avg_win=avg_win,
        avg_loss=avg_loss, rr_ratio=rr, max_drawdown=round(max_dd, 2),
        sharpe_ratio=sharpe, sortino_ratio=sortino,
        max_win_streak=max_w, max_loss_streak=max_l,
        avg_hold_days=avg_hold, expectancy=expectancy,
    )
