import pandas as pd
import pytest

from utils.indicators import atr, ema_last, ema_series, stop_price_from_atr


def test_ema_series_seeds_from_sma_then_smooths():
    values = [10.0, 12.0, 14.0, 16.0]

    result = ema_series(values, period=3)

    assert result[0] is None
    assert result[1] is None
    assert result[2] == pytest.approx(12.0)
    assert result[3] == pytest.approx(14.0)


def test_ema_last_returns_none_when_insufficient_data():
    assert ema_last([10.0, 11.0], period=3) is None


def test_atr_uses_true_range_with_previous_close():
    bars = pd.DataFrame(
        [
            {"high": 11.0, "low": 9.0, "close": 10.0},
            {"high": 13.0, "low": 10.0, "close": 12.0},
            {"high": 12.5, "low": 11.5, "close": 12.0},
        ]
    )

    assert atr(bars, period=3) == pytest.approx(2.0)


def test_atr_returns_none_when_period_not_available():
    bars = pd.DataFrame([{"high": 11.0, "low": 9.0, "close": 10.0}])

    assert atr(bars, period=14) is None


def test_stop_price_from_atr_uses_one_and_half_atr_below_entry():
    assert stop_price_from_atr(entry_price=20.0, atr_value=2.25) == pytest.approx(16.625)
