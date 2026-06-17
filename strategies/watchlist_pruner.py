from __future__ import annotations

import json
import logging
from pathlib import Path

import config
from config.settings import MAX_SWING_HOLD_DAYS, SWING_WATCHLIST_PATH

_log = logging.getLogger(__name__)


def should_keep_setup(setup: dict, latest_baseline: dict | None = None) -> bool:
    latest_baseline = latest_baseline or {}
    if setup.get("symbol") in config.blocked_tickers:
        return False
    if int(setup.get("age_days", 0)) > MAX_SWING_HOLD_DAYS:
        return False
    close = latest_baseline.get("previous_close", setup.get("close"))
    ema20 = latest_baseline.get("ema20")
    if close is not None and ema20 is not None and float(close) < float(ema20):
        return False
    return True


def prune_watchlist(watchlist: list[dict], baselines: dict[str, dict] | None = None) -> list[dict]:
    baselines = baselines or {}
    return [setup for setup in watchlist if should_keep_setup(setup, baselines.get(setup.get("symbol"), {}))]


def run_watchlist_pruner(path: str | Path = SWING_WATCHLIST_PATH, baselines: dict[str, dict] | None = None) -> list[dict]:
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        return []
    watchlist = json.loads(target.read_text())
    pruned = prune_watchlist(watchlist, baselines)
    target.write_text(json.dumps(pruned, indent=2))
    _log.info("WatchlistPruner | kept %d of %d setup(s)", len(pruned), len(watchlist))
    return pruned
