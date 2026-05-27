"""New York market session state and time utilities."""

from datetime import datetime, time
from enum import Enum
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

_PRE_OPEN      = time(4,  0)
_REGULAR_OPEN  = time(9, 30)
_REGULAR_CLOSE = time(16, 0)
_POST_CLOSE    = time(20, 0)

REGULAR_SESSION_MINUTES:  int = 390    # 9:30 AM – 4:00 PM  = 6.5 h × 60
EXTENDED_SESSION_MINUTES: int = 960    # 4:00 AM – 8:00 PM  = 16 h   × 60


class SessionState(str, Enum):
    CLOSED  = "closed"
    PRE     = "pre"
    REGULAR = "regular"
    POST    = "post"


def current_session() -> SessionState:
    t = datetime.now(tz=_ET).time()
    if t < _PRE_OPEN or t >= _POST_CLOSE:
        return SessionState.CLOSED
    if t < _REGULAR_OPEN:
        return SessionState.PRE
    if t < _REGULAR_CLOSE:
        return SessionState.REGULAR
    return SessionState.POST


def is_regular_session() -> bool:
    return current_session() == SessionState.REGULAR


def is_extended_session() -> bool:
    """True for pre-market, regular, and after-market (4:00 AM – 8:00 PM ET)."""
    return current_session() != SessionState.CLOSED


def elapsed_regular_minutes() -> float:
    """Minutes elapsed since 9:30 AM ET open. Returns 0.0 outside regular hours."""
    now      = datetime.now(tz=_ET)
    open_dt  = datetime.combine(now.date(), _REGULAR_OPEN,  tzinfo=_ET)
    close_dt = datetime.combine(now.date(), _REGULAR_CLOSE, tzinfo=_ET)
    if now < open_dt or now >= close_dt:
        return 0.0
    return (now - open_dt).total_seconds() / 60.0


def elapsed_extended_minutes() -> float:
    """Minutes elapsed since 4:00 AM ET. Returns 0.0 outside extended session."""
    now      = datetime.now(tz=_ET)
    open_dt  = datetime.combine(now.date(), _PRE_OPEN,   tzinfo=_ET)
    close_dt = datetime.combine(now.date(), _POST_CLOSE, tzinfo=_ET)
    if now < open_dt or now >= close_dt:
        return 0.0
    return (now - open_dt).total_seconds() / 60.0
