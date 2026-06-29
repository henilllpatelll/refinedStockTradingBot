import pandas as pd

from strategies.swing_routine import check_signal


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


def _context(**overrides):
    base = {
        "rs_rating": 92,
        "sector_rank": 1,
        "sector_rs": 0.04,
        "stock_rs": 0.12,
        "theme": "AI infrastructure",
    }
    base.update(overrides)
    return base


def test_routine_requires_weekly_trend_daily_trend_theme_rs_and_sector():
    bars = _bars([30.0] * 80 + [28.0 - i * 0.15 for i in range(80)])

    assert check_signal("BROKEN", bars, _context()) is None


def test_pullback_reversal_signal_uses_daily_support_and_drying_volume():
    closes = [30 + i * 0.1 for i in range(216)] + [51.0, 50.6, 50.3, 50.9]
    volumes = [220_000] * 216 + [180_000, 140_000, 105_000, 260_000]
    lows = [close * 0.99 for close in closes]
    bars = _bars(closes, volumes=volumes, lows=lows)
    ema20 = bars["close"].ewm(span=20, adjust=False).mean().iloc[-1]
    bars.loc[bars.index[-1], "low"] = ema20 * 0.995

    signal = check_signal("VRT", bars, _context())

    assert signal is not None
    assert signal["strategy_id"] == "ISR"
    assert signal["details"]["setup_type"] == "pullback_reversal"
    assert signal["details"]["entry_trigger"] == "reclaim_vwap_or_break_65m_range"


def test_tight_consolidation_breakout_requires_volume_expansion():
    closes = [30 + i * 0.12 for i in range(220)] + [56.5, 56.7, 56.6, 56.8, 56.65, 56.75, 57.5]
    highs = [close * 1.01 for close in closes]
    lows = [close * 0.99 for close in closes]
    for i in range(220, 226):
        highs[i] = 56.95
        lows[i] = 56.25
    highs[-1] = 57.8
    lows[-1] = 57.1
    volumes = [200_000] * 220 + [110_000] * 6 + [320_000]
    bars = _bars(closes, volumes=volumes, highs=highs, lows=lows)

    signal = check_signal("ANET", bars, _context())

    assert signal is not None
    assert signal["details"]["setup_type"] == "tight_consolidation_breakout"
    assert signal["details"]["range_high"] == 56.95


def test_earnings_gap_and_hold_requires_waiting_for_post_earnings_reaction():
    closes = [50 + i * 0.08 for i in range(220)] + [68.0, 70.5, 72.0]
    bars = _bars(closes, lows=[close * 0.99 for close in closes])

    signal = check_signal(
        "MU",
        bars,
        _context(catalyst_type="earnings_beat", earnings_beat_age_days=1, gap_pct=5.5, earnings_day_low=69.0),
    )

    assert signal is not None
    assert signal["details"]["setup_type"] == "earnings_gap_and_hold"
    assert signal["catalyst_type"] == "earnings_beat"


def test_routine_rejects_extended_or_risk_off_setups():
    closes = [20 + i * 0.2 for i in range(100)]
    bars = _bars(closes[:-1] + [60.0], volumes=[100_000] * 99 + [250_000])

    assert check_signal("CHASE", bars, _context()) is None
    assert check_signal("RISKOFF", _bars(closes), _context(market_regime="DOWNTREND")) is None
