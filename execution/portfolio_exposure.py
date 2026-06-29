from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import MAX_CONCURRENT_POSITIONS, MAX_DAILY_LOSS, MAX_SECTOR_POSITIONS
from execution.position_manager import open_positions
from execution.trade_logger import load_trade_records

_log = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")


def _load_sector_map() -> dict[str, str]:
    try:
        return json.loads(Path("config/sector_map.json").read_text())
    except Exception:
        return {}


def count_open_positions() -> int:
    return len(open_positions)


def count_sector_positions(sector: str, sector_map: dict[str, str]) -> int:
    count = 0
    for symbol, _strategy_id in open_positions:
        if sector_map.get(symbol, "") == sector:
            count += 1
    return count


def total_daily_realized_loss() -> float:
    today = datetime.now(_ET).date()
    records = load_trade_records()
    total = 0.0
    for record in records:
        exit_time = record.get("exit_time") or record.get("entry_time") or ""
        try:
            record_date = datetime.fromisoformat(exit_time).date()
        except Exception:
            continue
        if record_date != today:
            continue
        pnl = float(record.get("pnl", 0.0))
        if pnl < 0:
            total += pnl
    return total


def should_disable_entries() -> bool:
    return total_daily_realized_loss() <= -MAX_DAILY_LOSS


def can_add_position(symbol: str, sector_map: dict[str, str]) -> tuple[bool, str]:
    if count_open_positions() >= MAX_CONCURRENT_POSITIONS:
        return False, f"max concurrent positions reached ({MAX_CONCURRENT_POSITIONS})"
    sector = sector_map.get(symbol, "")
    if sector and count_sector_positions(sector, sector_map) >= MAX_SECTOR_POSITIONS:
        return False, f"max sector positions reached for {sector} ({MAX_SECTOR_POSITIONS})"
    return True, ""
