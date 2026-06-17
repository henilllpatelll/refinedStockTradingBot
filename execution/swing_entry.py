from __future__ import annotations

import json
import logging
from pathlib import Path

from config.rejection_tracker import rejection_tracker
from config.settings import (
    CONFIRMED_SETUPS_PATH,
    SIGNAL_SIZE_1_STRATEGY,
    SIGNAL_SIZE_2_STRATEGIES,
    SIGNAL_SIZE_3_PLUS_STRATEGIES,
)
from execution.position_manager import submit_swing_entry
from utils.market_data import fetch_latest_prices

_log = logging.getLogger(__name__)

_MAX_CHASE_PCT = 0.08  # skip entry if price has run >8% above EOD close


def _validate_entry_price(setup: dict, current_price: float) -> bool:
    symbol = setup["symbol"]
    strategy_id = setup.get("strategy_id", "")
    details = setup.get("details", {})
    eod_close = float(setup.get("close") or 0)

    if eod_close > 0 and current_price > eod_close * (1 + _MAX_CHASE_PCT):
        _log.info(
            "SwingEntry | %s %s skipped — price %.4f >%.0f%% above EOD close %.4f",
            symbol, strategy_id, current_price, _MAX_CHASE_PCT * 100, eod_close,
        )
        return False

    if strategy_id == "S1":
        level = details.get("prior_swing_high")
        if level and current_price < float(level):
            _log.info("SwingEntry | %s S1 skipped — price %.4f below breakout level %.4f", symbol, current_price, level)
            return False

    elif strategy_id == "S2":
        level = details.get("high_52w")
        if level and current_price < float(level):
            _log.info("SwingEntry | %s S2 skipped — price %.4f below 52w high %.4f", symbol, current_price, level)
            return False

    elif strategy_id == "S4":
        level = details.get("earnings_day_low")
        if level and current_price < float(level):
            _log.info("SwingEntry | %s S4 skipped — price %.4f below earnings day low %.4f", symbol, current_price, level)
            return False

    elif strategy_id == "S5":
        ema20 = details.get("ema20")
        if ema20:
            deviation = abs(current_price - float(ema20)) / float(ema20)
            if deviation > 0.03:
                _log.info(
                    "SwingEntry | %s S5 skipped — price %.4f is %.1f%% from EMA20 %.4f",
                    symbol, current_price, deviation * 100, ema20,
                )
                return False

    elif strategy_id in ("S9", "S12"):
        if eod_close > 0 and current_price < eod_close * 0.97:
            _log.info(
                "SwingEntry | %s %s skipped — price %.4f fell >3%% below EOD close %.4f",
                symbol, strategy_id, current_price, eod_close,
            )
            return False

    elif strategy_id == "S13":
        level = details.get("previous_close")
        if level and current_price < float(level):
            _log.info("SwingEntry | %s S13 skipped — price %.4f below previous close %.4f", symbol, current_price, level)
            return False

    return True


def position_budget_for_signal_count(signal_count: int) -> float:
    if signal_count <= 1:
        return SIGNAL_SIZE_1_STRATEGY
    if signal_count == 2:
        return SIGNAL_SIZE_2_STRATEGIES
    return SIGNAL_SIZE_3_PLUS_STRATEGIES


def shares_for_setup(setup: dict) -> int:
    price = float(setup.get("limit_price") or setup.get("close") or 0.0)
    if price <= 0:
        return 0
    budget = position_budget_for_signal_count(int(setup.get("signal_count_for_symbol", 1)))
    return max(1, int(budget // price))


def load_confirmed_setups(path: str | Path = CONFIRMED_SETUPS_PATH) -> list[dict]:
    source = Path(path)
    if not source.exists() or source.stat().st_size == 0:
        return []
    return json.loads(source.read_text())


async def place_entry_orders(setups: list[dict]) -> list[dict]:
    submitted: list[dict] = []
    latest_prices = await fetch_latest_prices(list(dict.fromkeys(setup["symbol"] for setup in setups)))
    for setup in setups:
        limit_price = float(latest_prices.get(setup["symbol"]) or setup.get("limit_price") or setup.get("close"))
        if not _validate_entry_price(setup, limit_price):
            rejection_tracker.record(
                setup["symbol"], "entry", "level_invalidated_at_open",
                strategy_id=setup.get("strategy_id"),
                eod_close=setup.get("close"),
                live_price=round(limit_price, 4),
            )
            continue
        refreshed_setup = {**setup, "limit_price": limit_price}
        shares = shares_for_setup(refreshed_setup)
        if shares < 1:
            continue
        order_id = await submit_swing_entry(refreshed_setup, shares, limit_price)
        if order_id:
            submitted.append({**refreshed_setup, "shares": shares, "entry_order_id": order_id})
    return submitted


async def run_swing_entry() -> list[dict]:
    setups = load_confirmed_setups()
    submitted = await place_entry_orders(setups)
    _log.info("SwingEntry | submitted %d order(s)", len(submitted))
    return submitted
