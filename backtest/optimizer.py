from __future__ import annotations

import asyncio
import itertools
import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from backtest.backtester import run_backtest
from backtest.metrics import BacktestStats

_log = logging.getLogger(__name__)
_RESULTS_PATH = Path("backtest/results")


@dataclass
class OptimizationResult:
    strategy_id: str
    best_params: dict[str, Any]
    best_stats: BacktestStats
    all_results: list[dict]


async def optimize_strategy(
    strategy_id: str,
    symbols: list[str],
    start_date: date,
    end_date: date,
    *,
    param_grid: dict[str, list[Any]],
    objective: str = "sharpe_ratio",
) -> OptimizationResult:
    """Grid search over param_grid for a single strategy."""
    keys = list(param_grid.keys())
    combos = list(itertools.product(*[param_grid[k] for k in keys]))
    _log.info("Optimizer | %s: testing %d parameter combinations", strategy_id, len(combos))

    all_results: list[dict] = []
    best_score = float("-inf")
    best_params: dict = {}
    best_stats: BacktestStats | None = None

    for combo in combos:
        params = dict(zip(keys, combo))
        try:
            stats_map = await run_backtest(
                symbols, start_date, end_date,
                strategy_ids=[strategy_id],
                save_results=False,
                **{k: v for k, v in params.items() if k in ("slippage_pct", "max_hold_days")},
            )
        except Exception as exc:
            _log.warning("Optimizer | combo %s failed: %s", params, exc)
            continue

        stats = stats_map.get(strategy_id)
        if stats is None or stats.total_trades < 5:
            continue

        score = getattr(stats, objective, 0.0)
        result_entry = {
            **params,
            "trades": stats.total_trades,
            "win_rate": stats.win_rate,
            "total_pnl": stats.total_pnl,
            "sharpe_ratio": stats.sharpe_ratio,
            "rr_ratio": stats.rr_ratio,
            objective: score,
        }
        all_results.append(result_entry)

        if score > best_score:
            best_score = score
            best_params = params
            best_stats = stats

    if best_stats is None:
        _log.warning("Optimizer | %s: no valid results found", strategy_id)
        best_stats = BacktestStats(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0, 0.0)

    _log.info("Optimizer | %s best params=%s %s=%.3f", strategy_id, best_params, objective, best_score)
    _save_optimization_results(strategy_id, all_results, best_params, start_date, end_date)
    return OptimizationResult(
        strategy_id=strategy_id,
        best_params=best_params,
        best_stats=best_stats,
        all_results=all_results,
    )


def _save_optimization_results(
    strategy_id: str,
    results: list[dict],
    best: dict,
    start_date: date,
    end_date: date,
) -> None:
    _RESULTS_PATH.mkdir(parents=True, exist_ok=True)
    label = f"{strategy_id}_{start_date}_{end_date}"
    payload = {"strategy_id": strategy_id, "best_params": best, "all_results": results}
    (_RESULTS_PATH / f"optimize_{label}.json").write_text(json.dumps(payload, indent=2))


async def optimize_all_strategies(
    symbols: list[str],
    start_date: date,
    end_date: date,
    *,
    objective: str = "sharpe_ratio",
) -> dict[str, OptimizationResult]:
    """Run optimization for the institutional swing routine with default param grids."""
    default_grids = {
        "ISR": {"max_hold_days": [10, 15, 20], "slippage_pct": [0.001, 0.002]},
    }
    results: dict[str, OptimizationResult] = {}
    for strategy_id, grid in default_grids.items():
        result = await optimize_strategy(
            strategy_id, symbols, start_date, end_date,
            param_grid=grid, objective=objective,
        )
        results[strategy_id] = result
    return results


def optimize_all_sync(symbols: list[str], start_date: date, end_date: date, **kwargs) -> dict:
    return asyncio.run(optimize_all_strategies(symbols, start_date, end_date, **kwargs))
