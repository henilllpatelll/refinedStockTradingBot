from __future__ import annotations

import json
import logging
from pathlib import Path

import config
from agents.news_analyst import latest_catalysts_by_symbol
from config.settings import CONFIRMED_SETUPS_PATH, SWING_WATCHLIST_PATH
from utils.market_data import fetch_premarket_gaps

_log = logging.getLogger(__name__)


def load_watchlist(path: str | Path = SWING_WATCHLIST_PATH) -> list[dict]:
    source = Path(path)
    if not source.exists() or source.stat().st_size == 0:
        return []
    return json.loads(source.read_text())


def confirm_setup(setup: dict, news_by_symbol: dict[str, dict] | None = None, gap_by_symbol: dict[str, float] | None = None) -> dict | None:
    news_by_symbol = news_by_symbol or {}
    gap_by_symbol = gap_by_symbol or {}
    symbol = setup["symbol"]
    if symbol in config.blocked_tickers:
        return None
    gap_pct = float(gap_by_symbol.get(symbol, setup.get("gap_pct", 0.0)))
    if gap_pct <= -3.0:
        return None
    news = news_by_symbol.get(symbol, {})
    catalyst_type = news.get("catalyst_type") or setup.get("catalyst_type", "technical_breakout")
    return {**setup, "catalyst_type": catalyst_type, "premarket_gap_pct": gap_pct, "confirmed": True}


def save_confirmed_setups(setups: list[dict], path: str | Path = CONFIRMED_SETUPS_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(setups, indent=2))
    return target


async def run_premarket_filter(
    news_by_symbol: dict[str, dict] | None = None,
    gap_by_symbol: dict[str, float] | None = None,
) -> list[dict]:
    watchlist = load_watchlist()
    if news_by_symbol is None:
        news_by_symbol = latest_catalysts_by_symbol()
    if gap_by_symbol is None:
        gap_by_symbol = await fetch_premarket_gaps(list(dict.fromkeys(item["symbol"] for item in watchlist)))
    confirmed = [
        setup
        for setup in (
            confirm_setup(item, news_by_symbol, gap_by_symbol)
            for item in watchlist
        )
        if setup is not None
    ]
    save_confirmed_setups(confirmed)
    async with config._confirmed_lock:
        config.confirmed_setups[:] = confirmed
    _log.info("PremarketFilter | confirmed %d setup(s)", len(confirmed))
    return confirmed
