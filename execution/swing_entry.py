from __future__ import annotations

import json
import logging
from pathlib import Path

from config.settings import (
    CONFIRMED_SETUPS_PATH,
    SIGNAL_SIZE_1_STRATEGY,
    SIGNAL_SIZE_2_STRATEGIES,
    SIGNAL_SIZE_3_PLUS_STRATEGIES,
)
from execution.position_manager import submit_swing_entry

_log = logging.getLogger(__name__)


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
    for setup in setups:
        shares = shares_for_setup(setup)
        if shares < 1:
            continue
        limit_price = float(setup.get("limit_price") or setup.get("close"))
        order_id = await submit_swing_entry(setup, shares, limit_price)
        if order_id:
            submitted.append({**setup, "shares": shares, "entry_order_id": order_id})
    return submitted


async def run_swing_entry() -> list[dict]:
    setups = load_confirmed_setups()
    submitted = await place_entry_orders(setups)
    _log.info("SwingEntry | submitted %d order(s)", len(submitted))
    return submitted
