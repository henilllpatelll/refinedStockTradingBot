from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

from config.settings import EARNINGS_CALENDAR_PATH, SWING_UNIVERSE_PATH
from utils.finnhub import fetch_earnings_calendar

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


def load_universe(path: str | Path = SWING_UNIVERSE_PATH) -> list[str]:
    source = Path(path)
    if not source.exists() or source.stat().st_size == 0:
        return []
    return json.loads(source.read_text())


def run_earnings_calendar_fetch(
    events: list[dict] | None = None,
    *,
    days_ahead: int = 14,
    path: str | Path = EARNINGS_CALENDAR_PATH,
) -> list[dict]:
    if events is None:
        start = date.today()
        end = start + timedelta(days=days_ahead)
        try:
            events = fetch_earnings_calendar(start, end, symbols=load_universe())
        except RuntimeError as exc:
            _log.warning("EarningsCalendar | Finnhub skipped: %s", exc)
            events = []
        except Exception as exc:
            _log.error("EarningsCalendar | Finnhub fetch failed: %s", exc)
            events = []
    save_earnings_calendar(events, path)
    _log.info("EarningsCalendar | saved %d event(s)", len(events))
    return events
