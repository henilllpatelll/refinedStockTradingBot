from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable
from datetime import datetime

import pandas as pd

from agents.news_analyst import latest_catalysts_by_symbol
from config.settings import (
    NEWS_CATALYSTS_PATH,
    SECTOR_MAP_PATH,
    SECTOR_RANKINGS_PATH,
    SWING_UNIVERSE_PATH,
    SWING_WATCHLIST_PATH,
)
from strategies.playbook import (
    s1_breakout,
    s2_52wk_high,
    s4_earnings_momentum,
    s5_pullback_ema20,
    s9_flag_pennant,
    s12_sector_rotation,
    s13_analyst_upgrade,
)
from utils.market_data import fetch_daily_bars

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


def _load_json(path: str | Path, default):
    source = Path(path)
    if not source.exists() or source.stat().st_size == 0:
        return default
    return json.loads(source.read_text())


def load_universe(path: str | Path = SWING_UNIVERSE_PATH) -> list[str]:
    return _load_json(path, [])


def _age_days(value: str | None) -> int | None:
    if not value:
        return None
    try:
        detected_at = datetime.fromisoformat(value)
    except ValueError:
        return None
    now = datetime.now(detected_at.tzinfo) if detected_at.tzinfo else datetime.now()
    return max(0, (now.date() - detected_at.date()).days)


def _relative_strength(df: pd.DataFrame, period: int = 5) -> float | None:
    if df.empty or "close" not in df or len(df) < 2:
        return None
    closes = [float(value) for value in df["close"].tail(period)]
    if len(closes) < 2 or closes[0] == 0:
        return None
    return (closes[-1] - closes[0]) / closes[0]


def build_context_by_symbol(
    bars_by_symbol: dict[str, pd.DataFrame],
    *,
    catalysts_path: str | Path = NEWS_CATALYSTS_PATH,
    sector_rankings_path: str | Path = SECTOR_RANKINGS_PATH,
    sector_map_path: str | Path = SECTOR_MAP_PATH,
) -> dict[str, dict]:
    context: dict[str, dict] = {symbol: {} for symbol in bars_by_symbol}

    for symbol, catalyst in latest_catalysts_by_symbol(path=catalysts_path).items():
        if symbol not in context:
            continue
        catalyst_type = catalyst.get("catalyst_type", "technical_breakout")
        age_days = _age_days(catalyst.get("detected_at"))
        context[symbol].update(
            {
                "catalyst_type": catalyst_type,
                "catalyst_age_days": age_days,
                "headline": catalyst.get("headline"),
            }
        )
        if catalyst_type == "analyst_upgrade":
            context[symbol]["analyst_upgrade_age_days"] = age_days
        if catalyst_type == "earnings_beat":
            context[symbol]["earnings_beat_age_days"] = age_days
            if "gap_pct" in catalyst:
                context[symbol]["gap_pct"] = catalyst["gap_pct"]
            if "earnings_day_low" in catalyst:
                context[symbol]["earnings_day_low"] = catalyst["earnings_day_low"]

    sector_rankings = {
        item.get("etf"): item
        for item in _load_json(sector_rankings_path, [])
        if item.get("etf")
    }
    sector_map = _load_json(sector_map_path, {})
    for symbol, etf in sector_map.items():
        if symbol not in context:
            continue
        ranking = sector_rankings.get(etf)
        stock_rs = _relative_strength(bars_by_symbol[symbol])
        if ranking is None or stock_rs is None:
            continue
        context[symbol].update(
            {
                "sector_etf": etf,
                "sector_rank": int(ranking.get("rank", 99)),
                "sector_rs": float(ranking.get("weekly_return", 0.0)),
                "stock_rs": stock_rs,
            }
        )

    return context


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
        symbols = load_universe()
        if not symbols:
            _log.warning("EODScanner | universe empty; writing empty watchlist")
            bars_by_symbol = {}
        else:
            bars_by_symbol = await fetch_daily_bars(symbols)
    built_context = build_context_by_symbol(bars_by_symbol)
    if context_by_symbol:
        for symbol, override in context_by_symbol.items():
            built_context.setdefault(symbol, {}).update(override)
    watchlist = build_watchlist(bars_by_symbol, built_context)
    save_watchlist(watchlist)
    _log.info("EODScanner | saved %d setup(s)", len(watchlist))
    return watchlist
