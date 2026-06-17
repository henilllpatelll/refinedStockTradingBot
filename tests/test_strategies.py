import pandas as pd

from strategies.playbook import (
    s1_breakout,
    s2_52wk_high,
    s4_earnings_momentum,
    s5_pullback_ema20,
    s9_flag_pennant,
    s12_sector_rotation,
    s13_analyst_upgrade,
)


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


def test_s1_breakout_requires_swing_high_rvol_and_recent_catalyst():
    bars = _bars(
        [10, 10.5, 10.8, 10.2, 11.6],
        volumes=[100_000, 100_000, 100_000, 100_000, 180_000],
        highs=[10.2, 10.9, 11.0, 10.4, 11.8],
    )

    signal = s1_breakout.check_signal("ABCD", bars, {"catalyst_age_days": 2})

    assert signal is not None
    assert signal["strategy_id"] == "S1"
    assert signal["catalyst_type"] == "technical_breakout"


def test_s2_52wk_high_requires_new_high_volume_and_ema50():
    closes = [20 + i * 0.2 for i in range(60)]
    bars = _bars(closes, volumes=[100_000] * 59 + [180_000])

    signal = s2_52wk_high.check_signal("HIGH", bars, {})

    assert signal is not None
    assert signal["strategy_id"] == "S2"


def test_s4_earnings_momentum_requires_recent_beat_gap_and_support():
    bars = _bars([29.0, 30.0, 32.0], lows=[28.5, 29.5, 31.5])

    signal = s4_earnings_momentum.check_signal(
        "BEAT",
        bars,
        {"earnings_beat_age_days": 1, "gap_pct": 4.5, "earnings_day_low": 31.0},
    )

    assert signal is not None
    assert signal["catalyst_type"] == "earnings_beat"


def test_s5_pullback_requires_uptrend_ema20_touch_and_declining_volume():
    closes = [20 + i * 0.15 for i in range(60)]
    lows = [close * 0.99 for close in closes]
    bars = _bars(closes, volumes=[200_000] * 57 + [160_000, 130_000, 100_000], lows=lows)
    ema20 = bars["close"].tail(20).mean()
    bars.loc[bars.index[-1], "low"] = ema20 * 1.01

    signal = s5_pullback_ema20.check_signal("PULL", bars, {})

    assert signal is not None
    assert signal["strategy_id"] == "S5"


def test_s9_flag_pennant_requires_impulse_consolidation_and_volume_breakout():
    bars = _bars(
        [10.0, 10.7, 10.75, 10.72, 10.8, 11.1],
        volumes=[100_000, 130_000, 90_000, 85_000, 80_000, 220_000],
        highs=[10.1, 10.8, 10.86, 10.85, 10.86, 11.2],
        lows=[9.9, 10.6, 10.62, 10.63, 10.64, 10.9],
    )

    signal = s9_flag_pennant.check_signal("FLAG", bars, {})

    assert signal is not None
    assert signal["strategy_id"] == "S9"


def test_s12_sector_rotation_requires_top_sector_stock_rs_and_ema20():
    closes = [30 + i * 0.1 for i in range(30)]
    bars = _bars(closes)

    signal = s12_sector_rotation.check_signal(
        "SECT",
        bars,
        {"sector_rank": 2, "stock_rs": 1.12, "sector_rs": 1.05},
    )

    assert signal is not None
    assert signal["catalyst_type"] == "sector_tailwind"


def test_s13_analyst_upgrade_requires_recent_upgrade_price_and_volume():
    bars = _bars([40.0, 41.0], volumes=[100_000, 125_000])

    signal = s13_analyst_upgrade.check_signal(
        "UPGD",
        bars,
        {"analyst_upgrade_age_days": 1},
    )

    assert signal is not None
    assert signal["strategy_id"] == "S13"
