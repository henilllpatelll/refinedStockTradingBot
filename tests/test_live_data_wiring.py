from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

import config
from agents import news_analyst, sector_ranker
from strategies import eod_scanner, premarket_filter


def _bars(closes, volumes=None, highs=None, lows=None):
    volumes = volumes or [100_000] * len(closes)
    highs = highs or [close * 1.01 for close in closes]
    lows = lows or [close * 0.99 for close in closes]
    return pd.DataFrame(
        [
            {"open": close * 0.99, "high": high, "low": low, "close": close, "volume": volume}
            for close, high, low, volume in zip(closes, highs, lows, volumes)
        ]
    )


@pytest.fixture(autouse=True)
def reset_shared_state():
    config.greenlighted_tickers.clear()
    config.blocked_tickers.clear()
    config.macro_alerts.clear()
    yield
    config.greenlighted_tickers.clear()
    config.blocked_tickers.clear()
    config.macro_alerts.clear()


@pytest.mark.asyncio
async def test_eod_scan_fetches_daily_bars_when_no_bars_are_supplied(monkeypatch):
    # "HIGH" outperforms "LAGGARD" so it gets RS rating 99 (>= RS_MIN_PERCENTILE=70)
    async def fake_fetch_daily_bars(symbols):
        assert set(symbols) == {"HIGH", "LAGGARD"}
        closes_high = [20 + i * 0.08 for i in range(220)] + [38.0, 38.1, 38.05, 38.2, 38.15, 38.25, 39.1]
        closes_lag = [10 + i * 0.01 for i in range(60)]   # flat — low RS
        return {
            "HIGH": _bars(closes_high, volumes=[100_000] * 220 + [70_000] * 6 + [180_000]),
            "LAGGARD": _bars(closes_lag, volumes=[100_000] * 60),
        }

    monkeypatch.setattr(eod_scanner, "load_universe", lambda: ["HIGH", "LAGGARD"])
    monkeypatch.setattr(eod_scanner, "fetch_daily_bars", fake_fetch_daily_bars)
    monkeypatch.setattr(
        eod_scanner,
        "build_context_by_symbol",
        lambda bars: {
            "HIGH": {"rs_rating": 99, "sector_rank": 1, "sector_rs": 0.03, "stock_rs": 0.12, "theme": "AI infrastructure"},
            "LAGGARD": {"rs_rating": 0, "sector_rank": 9, "sector_rs": -0.01, "stock_rs": 0.0},
        },
    )
    monkeypatch.setattr(eod_scanner, "save_watchlist", lambda watchlist: Path("unused.json"))

    watchlist = await eod_scanner.run_eod_scan()

    strategy_ids = [setup["strategy_id"] for setup in watchlist if setup["symbol"] == "HIGH"]
    assert strategy_ids == ["ISR"]


def test_context_builder_uses_persisted_catalysts_and_sector_map(tmp_path):
    now = datetime.now().isoformat()
    catalysts_path = tmp_path / "catalysts.json"
    catalysts_path.write_text(
        """
[
  {"symbol": "UPGD", "catalyst_type": "analyst_upgrade", "detected_at": "%s"},
  {"symbol": "BEAT", "catalyst_type": "earnings_beat", "detected_at": "%s"}
]
""".strip()
        % (now, now)
    )
    sector_rankings_path = tmp_path / "sector_rankings.json"
    sector_rankings_path.write_text('[{"etf": "XLK", "weekly_return": 0.05, "rank": 1}]')
    sector_map_path = tmp_path / "sector_map.json"
    sector_map_path.write_text('{"SECT": "XLK"}')
    bars_by_symbol = {
        "SECT": _bars([30, 31, 33, 34, 36, 38]),
        "UPGD": _bars([40, 41]),
        "BEAT": _bars([20, 21]),
    }

    context = eod_scanner.build_context_by_symbol(
        bars_by_symbol,
        catalysts_path=catalysts_path,
        sector_rankings_path=sector_rankings_path,
        sector_map_path=sector_map_path,
    )

    assert context["UPGD"]["analyst_upgrade_age_days"] == 0
    assert context["BEAT"]["earnings_beat_age_days"] == 0
    assert context["SECT"]["sector_rank"] == 1
    assert context["SECT"]["sector_rs"] == pytest.approx(0.05)
    assert context["SECT"]["stock_rs"] == pytest.approx((38 - 31) / 31)


@pytest.mark.asyncio
async def test_evaluate_news_persists_latest_catalyst(tmp_path, monkeypatch):
    catalysts_path = tmp_path / "news_catalysts.json"
    monkeypatch.setattr(news_analyst, "CATALYSTS_PATH", str(catalysts_path))

    await news_analyst.evaluate_news(
        {
            "headline": "Example price target raised after upgrade",
            "summary": "Analysts move estimates higher.",
            "symbols": ["UPGD"],
        }
    )

    latest = news_analyst.latest_catalysts_by_symbol(path=catalysts_path)
    assert latest["UPGD"]["catalyst_type"] == "analyst_upgrade"
    assert latest["UPGD"]["headline"] == "Example price target raised after upgrade"


@pytest.mark.asyncio
async def test_premarket_filter_fetches_gaps_and_uses_persisted_news(monkeypatch):
    monkeypatch.setattr(
        premarket_filter,
        "load_watchlist",
        lambda: [{"symbol": "UPGD", "strategy_id": "ISR", "close": 10.0}],
    )

    async def fake_fetch_gaps(symbols):
        assert symbols == ["UPGD"]
        return {"UPGD": 1.4}

    monkeypatch.setattr(premarket_filter, "fetch_premarket_gaps", fake_fetch_gaps)
    monkeypatch.setattr(
        premarket_filter,
        "latest_catalysts_by_symbol",
        lambda: {"UPGD": {"catalyst_type": "analyst_upgrade"}},
    )
    monkeypatch.setattr(premarket_filter, "save_confirmed_setups", lambda setups: Path("unused.json"))

    confirmed = await premarket_filter.run_premarket_filter()

    assert confirmed[0]["catalyst_type"] == "analyst_upgrade"
    assert confirmed[0]["premarket_gap_pct"] == pytest.approx(1.4)


def test_sector_ranker_fetches_weekly_returns_when_not_supplied(monkeypatch):
    monkeypatch.setattr(
        sector_ranker,
        "fetch_weekly_returns_sync",
        lambda symbols: {"XLK": 0.05, "XLF": -0.01},
    )
    monkeypatch.setattr(sector_ranker, "save_sector_rankings", lambda rankings: Path("unused.json"))

    rankings = sector_ranker.run_sector_ranking()

    assert rankings[0]["etf"] == "XLK"
    assert rankings[0]["rank"] == 1
