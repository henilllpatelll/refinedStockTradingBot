import pytest
import pandas as pd

from utils.market_regime import _distribution_days, _sma, _symbol_regime, get_market_regime


# ---------------------------------------------------------------------------
# _sma
# ---------------------------------------------------------------------------

def test_sma_returns_none_when_insufficient_data():
    assert _sma([1.0, 2.0], 5) is None


def test_sma_correct_value():
    assert _sma([1.0, 2.0, 3.0, 4.0, 5.0], 3) == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# _distribution_days
# ---------------------------------------------------------------------------

def _make_df(closes: list[float], volumes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": closes, "volume": volumes})


def test_distribution_days_zero_when_no_down_days():
    # All closes flat on average volume → 0 distribution days
    n = 80
    closes = [100.0] * n
    volumes = [500_000] * n
    df = _make_df(closes, volumes)
    assert _distribution_days(df) == 0


def test_distribution_days_counts_high_volume_down_days():
    n = 80
    closes = [100.0] * n
    volumes = [500_000] * n
    # Two down days with high volume in the last 25 bars
    closes[-10] = closes[-11] * (1 - 0.005)   # -0.5% — qualifies
    volumes[-10] = 800_000                     # above 500k average
    closes[-5] = closes[-6] * (1 - 0.003)     # -0.3% — qualifies
    volumes[-5] = 700_000
    df = _make_df(closes, volumes)
    assert _distribution_days(df) == 2


def test_distribution_days_ignores_low_volume_down_days():
    n = 80
    closes = [100.0] * n
    volumes = [500_000] * n
    # Down day but volume BELOW average → should not count
    closes[-10] = closes[-11] * (1 - 0.005)
    volumes[-10] = 200_000
    df = _make_df(closes, volumes)
    assert _distribution_days(df) == 0


def test_distribution_days_ignores_small_declines():
    n = 80
    closes = [100.0] * n
    volumes = [500_000] * n
    # Down day but only -0.1% (below -0.2% threshold)
    closes[-10] = closes[-11] * (1 - 0.001)
    volumes[-10] = 800_000
    df = _make_df(closes, volumes)
    assert _distribution_days(df) == 0


def test_distribution_days_returns_zero_when_insufficient_data():
    df = _make_df([100.0] * 5, [500_000] * 5)
    assert _distribution_days(df) == 0


# ---------------------------------------------------------------------------
# _symbol_regime
# ---------------------------------------------------------------------------

def _trending_df(n: int = 210, slope: float = 0.1, base: float = 100.0) -> pd.DataFrame:
    closes = [base + i * slope for i in range(n)]
    volumes = [500_000] * n
    return pd.DataFrame({"close": closes, "volume": volumes})


def test_symbol_regime_uptrend_above_both_smas():
    # Price trending up — close well above SMA50 and SMA200
    df = _trending_df(n=210, slope=0.1, base=100.0)
    assert _symbol_regime(df) == "UPTREND"


def test_symbol_regime_downtrend_below_sma200():
    # Build 210 bars that trend up then crash below SMA200
    n = 210
    closes = [100.0 + i * 0.1 for i in range(n)]
    # Force last close way below the SMA200
    closes[-1] = 50.0
    df = pd.DataFrame({"close": closes, "volume": [500_000] * n})
    assert _symbol_regime(df) == "DOWNTREND"


def test_symbol_regime_neutral_below_sma50_above_sma200():
    # 150 bars at 80, then 60 bars rising to 120.
    # SMA200 ≈ (150*80 + 50*120)/200 = 90  →  close (118) is above it.
    # SMA50 ≈ 120 (all last 50 were ~120) →  close (118) is below it.
    closes = [80.0] * 150 + [120.0] * 59 + [118.0]
    assert len(closes) == 210
    df = pd.DataFrame({"close": closes, "volume": [500_000] * 210})
    assert _symbol_regime(df) == "NEUTRAL"


def test_symbol_regime_downtrend_on_distribution_days():
    # Price above all SMAs but ≥5 distribution days
    n = 210
    closes = [100.0 + i * 0.1 for i in range(n)]
    volumes = [500_000] * n
    # Plant 5 distribution days in the last 25 bars (each -0.5% on high volume)
    for offset in [5, 8, 12, 16, 20]:
        idx = n - offset
        closes[idx] = closes[idx - 1] * 0.995
        volumes[idx] = 900_000
    df = pd.DataFrame({"close": closes, "volume": volumes})
    assert _symbol_regime(df) == "DOWNTREND"


def test_symbol_regime_returns_neutral_for_empty_df():
    assert _symbol_regime(pd.DataFrame({"close": [], "volume": []})) == "NEUTRAL"


# ---------------------------------------------------------------------------
# get_market_regime (integration via monkeypatch)
# ---------------------------------------------------------------------------

def _spy_uptrend_df():
    return _trending_df(n=210, slope=0.1, base=100.0)


def _spy_downtrend_df():
    n = 210
    closes = [100.0 + i * 0.1 for i in range(n)]
    closes[-1] = 50.0
    return pd.DataFrame({"close": closes, "volume": [500_000] * n})


@pytest.mark.asyncio
async def test_get_market_regime_uptrend(monkeypatch):
    async def fake_fetch(symbols, **kwargs):
        return {"SPY": _spy_uptrend_df(), "QQQ": _spy_uptrend_df()}

    import utils.market_regime as mr
    monkeypatch.setattr(mr, "fetch_daily_bars", fake_fetch)
    assert await get_market_regime() == "UPTREND"


@pytest.mark.asyncio
async def test_get_market_regime_downtrend_if_any_index_down(monkeypatch):
    async def fake_fetch(symbols, **kwargs):
        return {"SPY": _spy_uptrend_df(), "QQQ": _spy_downtrend_df()}

    import utils.market_regime as mr
    monkeypatch.setattr(mr, "fetch_daily_bars", fake_fetch)
    assert await get_market_regime() == "DOWNTREND"


@pytest.mark.asyncio
async def test_get_market_regime_neutral_when_mixed(monkeypatch):
    # One uptrend, one neutral → NEUTRAL
    # Same construction as test_symbol_regime_neutral_below_sma50_above_sma200
    closes_neutral = [80.0] * 150 + [120.0] * 59 + [118.0]
    df_neutral = pd.DataFrame({"close": closes_neutral, "volume": [500_000] * 210})

    async def fake_fetch(symbols, **kwargs):
        return {"SPY": _spy_uptrend_df(), "QQQ": df_neutral}

    import utils.market_regime as mr
    monkeypatch.setattr(mr, "fetch_daily_bars", fake_fetch)
    assert await get_market_regime() == "NEUTRAL"


@pytest.mark.asyncio
async def test_get_market_regime_defaults_neutral_on_fetch_error(monkeypatch):
    async def fake_fetch(symbols, **kwargs):
        raise RuntimeError("network error")

    import utils.market_regime as mr
    monkeypatch.setattr(mr, "fetch_daily_bars", fake_fetch)
    assert await get_market_regime() == "NEUTRAL"


@pytest.mark.asyncio
async def test_get_market_regime_defaults_neutral_when_no_data(monkeypatch):
    async def fake_fetch(symbols, **kwargs):
        return {}

    import utils.market_regime as mr
    monkeypatch.setattr(mr, "fetch_daily_bars", fake_fetch)
    assert await get_market_regime() == "NEUTRAL"
