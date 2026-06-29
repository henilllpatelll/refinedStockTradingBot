from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_log = logging.getLogger(__name__)
_MACRO_EVENTS_PATH = Path("config/macro_events.json")

_macro_events: list[tuple[datetime, str]] = []


def load_macro_events() -> None:
    global _macro_events
    if not _MACRO_EVENTS_PATH.exists():
        _macro_events = []
        return
    try:
        raw = json.loads(_MACRO_EVENTS_PATH.read_text())
        _macro_events = [
            (datetime.fromisoformat(item["dt"]).replace(tzinfo=_ET), item["name"])
            for item in raw
        ]
        _log.info("MacroCalendar | loaded %d event(s)", len(_macro_events))
    except Exception as exc:
        _log.warning("MacroCalendar | failed to load events: %s", exc)
        _macro_events = []


def save_macro_events(events: list[tuple[datetime, str]]) -> None:
    _MACRO_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = [{"dt": dt.isoformat(), "name": name} for dt, name in events]
    _MACRO_EVENTS_PATH.write_text(json.dumps(raw, indent=2))


def add_macro_event(dt: datetime, name: str) -> None:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_ET)
    _macro_events.append((dt, name))
    save_macro_events(_macro_events)


def is_in_freeze_window(now: datetime, freeze_before_min: int = 30, freeze_after_min: int = 15) -> tuple[bool, str]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=_ET)
    for event_dt, event_name in _macro_events:
        freeze_start = event_dt - timedelta(minutes=freeze_before_min)
        freeze_end = event_dt + timedelta(minutes=freeze_after_min)
        if freeze_start <= now <= freeze_end:
            return True, event_name
    return False, ""


def get_freeze_minutes_remaining(now: datetime, freeze_before_min: int = 30, freeze_after_min: int = 15) -> int:
    frozen, _ = is_in_freeze_window(now, freeze_before_min, freeze_after_min)
    if not frozen:
        return 0
    if now.tzinfo is None:
        now = now.replace(tzinfo=_ET)
    for event_dt, _ in _macro_events:
        freeze_end = event_dt + timedelta(minutes=freeze_after_min)
        if now <= freeze_end:
            return max(0, int((freeze_end - now).total_seconds() / 60))
    return 0


load_macro_events()
