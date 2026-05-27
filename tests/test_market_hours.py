"""Unit tests for config.market_hours — all datetime.now calls patched to fixed values."""
from datetime import datetime as _RealDt, date
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from config.market_hours import (
    SessionState,
    current_session,
    elapsed_extended_minutes,
    elapsed_regular_minutes,
    is_extended_session,
    is_regular_session,
)

_ET  = ZoneInfo("America/New_York")
_DAY = date(2024, 1, 15)   # Monday; no DST edge case


def _et(hour: int, minute: int = 0) -> _RealDt:
    return _RealDt(_DAY.year, _DAY.month, _DAY.day, hour, minute, tzinfo=_ET)


class TestCurrentSession:
    @pytest.mark.parametrize("hour,minute,expected", [
        (3,  59, SessionState.CLOSED),
        (4,   0, SessionState.PRE),
        (9,  29, SessionState.PRE),
        (9,  30, SessionState.REGULAR),
        (15, 59, SessionState.REGULAR),
        (16,  0, SessionState.POST),
        (19, 59, SessionState.POST),
        (20,  0, SessionState.CLOSED),
        (23,  0, SessionState.CLOSED),
    ])
    def test_session_boundaries(self, hour, minute, expected):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(hour, minute)
            assert current_session() == expected

    def test_session_state_enum_values(self):
        assert SessionState.CLOSED  == "closed"
        assert SessionState.PRE     == "pre"
        assert SessionState.REGULAR == "regular"
        assert SessionState.POST    == "post"


class TestHelpers:
    def test_is_regular_true_during_session(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(11, 0)
            assert is_regular_session() is True

    def test_is_regular_false_in_pre(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(5, 0)
            assert is_regular_session() is False

    def test_is_regular_false_after_close(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(17, 0)
            assert is_regular_session() is False

    def test_is_extended_true_in_pre(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(6, 0)
            assert is_extended_session() is True

    def test_is_extended_true_in_regular(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(10, 0)
            assert is_extended_session() is True

    def test_is_extended_true_in_post(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(17, 0)
            assert is_extended_session() is True

    def test_is_extended_false_when_closed(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(1, 0)
            assert is_extended_session() is False


class TestElapsedRegularMinutes:
    def test_at_open_is_zero(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(9, 30)
            m.combine.side_effect = _RealDt.combine
            assert elapsed_regular_minutes() == pytest.approx(0.0, abs=1e-3)

    def test_one_hour_into_session(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(10, 30)
            m.combine.side_effect = _RealDt.combine
            assert elapsed_regular_minutes() == pytest.approx(60.0, abs=1e-3)

    def test_ninety_minutes_in(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(11, 0)
            m.combine.side_effect = _RealDt.combine
            assert elapsed_regular_minutes() == pytest.approx(90.0, abs=1e-3)

    def test_zero_before_open(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(7, 0)
            m.combine.side_effect = _RealDt.combine
            assert elapsed_regular_minutes() == 0.0

    def test_zero_after_close(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(16, 30)
            m.combine.side_effect = _RealDt.combine
            assert elapsed_regular_minutes() == 0.0


class TestElapsedExtendedMinutes:
    def test_at_pre_open_is_zero(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(4, 0)
            m.combine.side_effect = _RealDt.combine
            assert elapsed_extended_minutes() == pytest.approx(0.0, abs=1e-3)

    def test_one_hour_into_pre(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(5, 0)
            m.combine.side_effect = _RealDt.combine
            assert elapsed_extended_minutes() == pytest.approx(60.0, abs=1e-3)

    def test_zero_when_closed(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(2, 0)
            m.combine.side_effect = _RealDt.combine
            assert elapsed_extended_minutes() == 0.0

    def test_zero_after_post_close(self):
        with patch("config.market_hours.datetime") as m:
            m.now.return_value = _et(21, 0)
            m.combine.side_effect = _RealDt.combine
            assert elapsed_extended_minutes() == 0.0
