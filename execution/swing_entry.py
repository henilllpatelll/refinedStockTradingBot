from __future__ import annotations

import json
import logging
from pathlib import Path

from config.rejection_tracker import rejection_tracker
from config.settings import CONFIRMED_SETUPS_PATH, MAX_POSITION_COST, MAX_RISK_PER_TRADE
from execution.position_manager import open_positions, submit_swing_entry
from utils.market_data import fetch_latest_completed_65min_bars, fetch_latest_prices

_log = logging.getLogger(__name__)

_MAX_CHASE_PCT = 0.08


def _validate_entry_price(setup: dict, current_price: float) -> bool:
    symbol = setup["symbol"]
    strategy_id = setup.get("strategy_id", "")
    details = setup.get("details", {})
    eod_close = float(setup.get("close") or 0)

    if eod_close > 0 and current_price > eod_close * (1 + _MAX_CHASE_PCT):
        _log.info(
            "SwingEntry | %s %s skipped - price %.4f >%.0f%% above EOD close %.4f",
            symbol, strategy_id, current_price, _MAX_CHASE_PCT * 100, eod_close,
        )
        return False

    if strategy_id != "ISR":
        _log.info("SwingEntry | %s skipped - unsupported strategy_id=%s", symbol, strategy_id)
        return False

    support = details.get("support_level")
    if support and current_price < float(support):
        _log.info("SwingEntry | %s ISR skipped - price %.4f below support %.4f", symbol, current_price, support)
        return False

    return True


def _entry_trigger_level(setup: dict, bar: dict) -> float | None:
    details = setup.get("details", {})
    for key in ("range_high", "prior_high", "high_52w", "earnings_day_low"):
        if details.get(key) is not None:
            return float(details[key])
    if bar.get("range_high") is not None:
        return float(bar["range_high"])
    return None


def _confirm_entry_timing(setup: dict, bar: dict) -> bool:
    """Confirm the routine's 65-minute/VWAP entry timing."""
    symbol = setup["symbol"]
    strategy_id = setup.get("strategy_id", "")
    details = setup.get("details", {})
    eod_close = float(setup.get("close") or 0)
    bar_close = float(bar["close"])

    if strategy_id == "ISR":
        vwap = bar.get("vwap")
        if vwap is None or bar_close < float(vwap):
            _log.info("SwingEntry | %s ISR 65m failed - close %.4f below VWAP %s", symbol, bar_close, vwap)
            return False

        bar_range = float(bar["high"]) - float(bar["low"])
        strong_close = bar_range <= 0 or bar_close >= float(bar["low"]) + bar_range * 0.6
        if not strong_close:
            _log.info("SwingEntry | %s ISR 65m failed - weak close inside bar", symbol)
            return False

        setup_type = details.get("setup_type", "")
        trigger_level = _entry_trigger_level(setup, bar)
        needs_trigger = setup_type in {"tight_consolidation_breakout", "new_high_breakout", "earnings_gap_and_hold"}
        if needs_trigger and trigger_level is not None and bar_close < trigger_level:
            _log.info("SwingEntry | %s ISR 65m failed - close %.4f below trigger %.4f", symbol, bar_close, trigger_level)
            return False
        if not needs_trigger and eod_close > 0 and bar_close < eod_close * 0.97:
            _log.info("SwingEntry | %s ISR 65m failed - close %.4f fell >3%% below EOD close %.4f", symbol, bar_close, eod_close)
            return False
        return True

    return False


def shares_for_setup(setup: dict) -> int:
    price = float(setup.get("limit_price") or setup.get("close") or 0.0)
    atr_val = float(setup.get("atr_14") or 0.0)
    if price <= 0:
        return 0
    risk_per_share = 1.5 * atr_val if atr_val > 0 else price * 0.05
    shares = int(MAX_RISK_PER_TRADE / risk_per_share)
    position_size_pct = float(setup.get("details", {}).get("position_size_pct", 1.0))
    max_cost = MAX_POSITION_COST * max(0.25, min(position_size_pct, 1.0))
    max_by_cost = int(max_cost / (price * 1.02))
    return max(1, min(shares, max_by_cost))


def load_confirmed_setups(path: str | Path = CONFIRMED_SETUPS_PATH) -> list[dict]:
    source = Path(path)
    if not source.exists() or source.stat().st_size == 0:
        return []
    return json.loads(source.read_text())


async def place_entry_orders(
    setups: list[dict],
    hourly_bars: dict[str, dict] | None = None,
) -> list[dict]:
    from execution.portfolio_exposure import can_add_position, should_disable_entries

    try:
        sector_map = json.loads(Path("config/sector_map.json").read_text())
    except Exception:
        sector_map = {}

    if should_disable_entries():
        _log.warning("SwingEntry | daily loss circuit breaker active - skipping all entries")
        return []

    submitted: list[dict] = []
    latest_prices = await fetch_latest_prices(list(dict.fromkeys(setup["symbol"] for setup in setups)))
    for setup in setups:
        symbol = setup["symbol"]

        allowed, reason = can_add_position(symbol, sector_map)
        if not allowed:
            _log.info("SwingEntry | %s skipped - %s", symbol, reason)
            rejection_tracker.record(symbol, "entry", reason, strategy_id=setup.get("strategy_id"))
            continue

        if hourly_bars is not None:
            bar = hourly_bars.get(symbol)
            if bar is None:
                _log.info("SwingEntry | %s skipped - no 65-minute bar available", symbol)
                rejection_tracker.record(symbol, "entry", "no_65m_bar", strategy_id=setup.get("strategy_id"))
                continue
            if not _confirm_entry_timing(setup, bar):
                rejection_tracker.record(
                    symbol,
                    "entry",
                    "entry_timing_failed",
                    strategy_id=setup.get("strategy_id"),
                    bar_close=round(float(bar["close"]), 4),
                )
                continue

        limit_price = float(latest_prices.get(symbol) or setup.get("limit_price") or setup.get("close"))
        if not _validate_entry_price(setup, limit_price):
            rejection_tracker.record(
                symbol,
                "entry",
                "level_invalidated_at_entry",
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
    if not setups:
        _log.info("SwingEntry | no confirmed setups")
        return []
    setups = [s for s in setups if (s["symbol"], s.get("strategy_id", "")) not in open_positions]
    if not setups:
        _log.info("SwingEntry | all confirmed setups already in open positions")
        return []
    symbols = list(dict.fromkeys(s["symbol"] for s in setups))
    entry_bars = await fetch_latest_completed_65min_bars(symbols)
    _log.info("SwingEntry | fetched 65-minute bars for %d/%d symbols", len(entry_bars), len(symbols))
    submitted = await place_entry_orders(setups, entry_bars)
    _log.info("SwingEntry | submitted %d order(s)", len(submitted))
    return submitted
