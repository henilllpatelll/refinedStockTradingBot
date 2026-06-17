from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

import pandas as pd

from config.settings import SWING_WATCHLIST_PATH
from strategies.playbook import (
    s1_breakout,
    s2_52wk_high,
    s4_earnings_momentum,
    s5_pullback_ema20,
    s9_flag_pennant,
    s12_sector_rotation,
    s13_analyst_upgrade,
)

_log = logging.getLogger(__name__)
_PLAYBOOK: tuple[Callable[[str, pd.DataFrame, dict], dict | None], ...] = (
    s1_breakout.check_signal,
    s2_52wk_high.check_signal,
    s4_earnings_momentum.check_signal,
    s5_pullback_ema20.check_signal,
    s9_flag_pennant.check_signal,
    s12_sector_rotation.check_signal,
    s13_analyst_upgrade.check_signal,
)


def evaluate_symbol(symbol: str, bars: pd.DataFrame, context: dict | None = None) -> list[dict]:
    context = context or {}
    signals: list[dict] = []
    for check_signal in _PLAYBOOK:
        signal = check_signal(symbol, bars, context)
        if signal is not None:
            signals.append(signal)
    signal_count = len(signals)
    return [{**signal, "signal_count_for_symbol": signal_count} for signal in signals]


def build_watchlist(
    bars_by_symbol: dict[str, pd.DataFrame],
    context_by_symbol: dict[str, dict] | None = None,
) -> list[dict]:
    context_by_symbol = context_by_symbol or {}
    watchlist: list[dict] = []
    for symbol, bars in bars_by_symbol.items():
        watchlist.extend(evaluate_symbol(symbol, bars, context_by_symbol.get(symbol, {})))
    return sorted(watchlist, key=lambda item: (item["symbol"], item["strategy_id"]))


def save_watchlist(watchlist: list[dict], path: str | Path = SWING_WATCHLIST_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(watchlist, indent=2))
    return target


async def run_eod_scan(
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
    context_by_symbol: dict[str, dict] | None = None,
) -> list[dict]:
    if bars_by_symbol is None:
        _log.warning("EODScanner | no bar provider configured; writing empty watchlist")
        bars_by_symbol = {}
    watchlist = build_watchlist(bars_by_symbol, context_by_symbol)
    save_watchlist(watchlist)
    _log.info("EODScanner | saved %d setup(s)", len(watchlist))
    return watchlist
