from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta
from pathlib import Path

from backtest.bar_loader import load_bars, slice_bars_up_to
from backtest.metrics import BacktestStats, compute_stats
from backtest.order_simulator import SimulatedFill, simulate_fill

_log = logging.getLogger(__name__)
_RESULTS_PATH = Path("backtest/results")


def _import_strategies() -> dict:
    from strategies.swing_routine import check_signal

    return {"ISR": check_signal}


def _strategy_exit_params(strategy_id: str) -> dict:
    params = {
        "ISR": {"t1_target_pct": 0.10, "trail_stop_pct": 0.07},
    }
    return params.get(strategy_id, params["ISR"])


async def run_backtest(
    symbols: list[str],
    start_date: date,
    end_date: date,
    *,
    strategy_ids: list[str] | None = None,
    slippage_pct: float = 0.001,
    max_hold_days: int = 14,
    save_results: bool = True,
) -> dict[str, BacktestStats]:
    """Run backtest for the institutional swing routine over the date range."""
    strategies = _import_strategies()
    if strategy_ids:
        strategies = {k: v for k, v in strategies.items() if k in strategy_ids}

    _log.info("Backtest | loading bars for %d symbols %s -> %s", len(symbols), start_date, end_date)
    bars_by_symbol = await load_bars(symbols, start_date, end_date)
    if not bars_by_symbol:
        _log.warning("Backtest | no bars loaded")
        return {}

    fills_by_strategy: dict[str, list[SimulatedFill]] = {sid: [] for sid in strategies}

    current = start_date
    while current <= end_date:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        for symbol, full_bars in bars_by_symbol.items():
            bars_as_of = slice_bars_up_to(full_bars, current)
            if len(bars_as_of) < 50:
                continue

            for strategy_id, check_fn in strategies.items():
                try:
                    signal = check_fn(symbol, bars_as_of, {})
                except Exception:
                    continue
                if signal is None:
                    continue

                exit_params = _strategy_exit_params(strategy_id)
                fill = simulate_fill(
                    symbol=symbol,
                    strategy_id=strategy_id,
                    signal_date=current,
                    bars=full_bars,
                    slippage_pct=slippage_pct,
                    max_hold_days=max_hold_days,
                    catalyst_type=signal.get("catalyst_type", "technical_breakout"),
                    **exit_params,
                )
                if fill is not None:
                    fills_by_strategy[strategy_id].append(fill)

        current += timedelta(days=1)

    stats_by_strategy: dict[str, BacktestStats] = {}
    for strategy_id, fills in fills_by_strategy.items():
        stats = compute_stats(fills)
        stats_by_strategy[strategy_id] = stats
        _log.info("Backtest | %s -> %s", strategy_id, stats.summary())

    if save_results:
        _save_results(stats_by_strategy, fills_by_strategy, start_date, end_date)

    return stats_by_strategy


def _save_results(
    stats: dict[str, BacktestStats],
    fills: dict[str, list[SimulatedFill]],
    start_date: date,
    end_date: date,
) -> None:
    _RESULTS_PATH.mkdir(parents=True, exist_ok=True)
    label = f"{start_date}_{end_date}"
    summary = {
        sid: {
            "total_trades": s.total_trades, "win_rate": s.win_rate,
            "total_pnl": s.total_pnl, "rr_ratio": s.rr_ratio,
            "max_drawdown": s.max_drawdown, "sharpe_ratio": s.sharpe_ratio,
            "expectancy": s.expectancy,
        }
        for sid, s in stats.items()
    }
    (_RESULTS_PATH / f"summary_{label}.json").write_text(json.dumps(summary, indent=2))
    _log.info("Backtest | results saved to backtest/results/summary_%s.json", label)


def run_backtest_sync(
    symbols: list[str],
    start_date: date,
    end_date: date,
    **kwargs,
) -> dict[str, BacktestStats]:
    return asyncio.run(run_backtest(symbols, start_date, end_date, **kwargs))
