from __future__ import annotations

from pathlib import Path

import pytest

import config
from agents import earnings_calendar, news_analyst, sector_ranker


def test_earnings_calendar_fetch_uses_finnhub_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(earnings_calendar, "load_universe", lambda: ["NVDA"])
    monkeypatch.setattr(
        earnings_calendar,
        "fetch_earnings_calendar",
        lambda start, end, symbols=None: [
            {
                "symbol": "NVDA",
                "date": "2026-06-20",
                "hour": "amc",
                "eps_estimate": 1.25,
                "source": "finnhub",
            }
        ],
    )

    events = earnings_calendar.run_earnings_calendar_fetch(path=tmp_path / "earnings.json")

    assert events[0]["symbol"] == "NVDA"
    assert events[0]["source"] == "finnhub"


def test_sector_map_builder_saves_finnhub_symbol_map(tmp_path, monkeypatch):
    monkeypatch.setattr(sector_ranker, "load_universe", lambda: ["NVDA", "SLB"])
    monkeypatch.setattr(
        sector_ranker,
        "build_sector_map",
        lambda symbols: {"NVDA": "XLK", "SLB": "XLE"},
    )

    mapping = sector_ranker.run_sector_map_build(path=tmp_path / "sector_map.json")

    assert mapping == {"NVDA": "XLK", "SLB": "XLE"}


@pytest.mark.asyncio
async def test_upgrade_scan_records_structured_analyst_catalysts(tmp_path, monkeypatch):
    monkeypatch.setattr(news_analyst, "CATALYSTS_PATH", str(tmp_path / "catalysts.json"))
    monkeypatch.setattr(news_analyst, "load_universe", lambda: ["NVDA"])
    monkeypatch.setattr(
        news_analyst,
        "fetch_recent_upgrades",
        lambda symbols, days=2: [
            {
                "symbol": "NVDA",
                "catalyst_type": "analyst_upgrade",
                "headline": "Example Bank upgrades NVDA from Hold to Buy",
                "detected_at": "2026-06-16T12:00:00+00:00",
                "provider": "Example Bank",
                "source": "finnhub",
            }
        ],
    )
    config.greenlighted_tickers.clear()

    records = await news_analyst.run_structured_upgrade_scan()

    assert records[0]["symbol"] == "NVDA"
    assert news_analyst.latest_catalysts_by_symbol(path=tmp_path / "catalysts.json")["NVDA"]["source"] == "finnhub"
    assert "NVDA" in config.greenlighted_tickers
