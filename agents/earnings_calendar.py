from __future__ import annotations

import json
import logging
from pathlib import Path

from config.settings import EARNINGS_CALENDAR_PATH

_log = logging.getLogger(__name__)


def save_earnings_calendar(events: list[dict], path: str | Path = EARNINGS_CALENDAR_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(events, indent=2))
    return target


def load_earnings_calendar(path: str | Path = EARNINGS_CALENDAR_PATH) -> list[dict]:
    source = Path(path)
    if not source.exists() or source.stat().st_size == 0:
        return []
    return json.loads(source.read_text())


def run_earnings_calendar_fetch(events: list[dict] | None = None) -> list[dict]:
    events = events or []
    save_earnings_calendar(events)
    _log.info("EarningsCalendar | saved %d event(s)", len(events))
    return events
