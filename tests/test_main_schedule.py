from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

import main


_ET = ZoneInfo("America/New_York")


def test_entry_window_skips_after_cutoff():
    now = datetime(2026, 6, 17, 10, 0, tzinfo=_ET)

    assert main._stage_status(time(9, 30), now, cutoff=time(9, 45)) == "missed"


def test_entry_window_runs_inside_window():
    now = datetime(2026, 6, 17, 9, 35, tzinfo=_ET)

    assert main._stage_status(time(9, 30), now, cutoff=time(9, 45)) == "due"


def test_main_runtime_no_longer_calls_structured_finnhub_upgrades():
    assert "run_structured_upgrade_scan" not in main._weekday_pipeline.__code__.co_names


@pytest.mark.asyncio
async def test_run_daily_cycle_loads_position_state_before_weekday_pipeline(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "load_position_state", lambda: calls.append("load"))
    monkeypatch.setattr(main, "audit_untracked_alpaca_positions", lambda: calls.append("audit") or [])

    async def fake_weekday(logger):
        calls.append("weekday")

    monkeypatch.setattr(main, "_weekday_pipeline", fake_weekday)

    await main._run_daily_cycle(datetime(2026, 6, 17, 6, 0, tzinfo=_ET), main.logging.getLogger("test"))

    assert calls == ["load", "audit", "weekday"]
