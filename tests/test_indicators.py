"""Unit tests for indicator helpers: EMA step, indicator filter, and lx limit-price helper."""
import pytest

from execution.position_manager import _lx, _SELL_OFFSET
from execution.trackB_realtime import (
    SymbolState,
    _ema_step,
    _indicator_reject_reason,
)


class TestEmaStep:
    def test_first_tick_seeds_with_price(self):
        ema, n = _ema_step(0.0, 0, 10.0, 9)
        assert ema == 10.0
        assert n == 1

    def test_second_tick_applies_smoothing(self):
        ema1, n1 = _ema_step(0.0, 0, 10.0, 9)
        ema2, n2 = _ema_step(ema1, n1, 12.0, 9)
        k        = 2.0 / (9 + 1)
        expected = 10.0 + k * (12.0 - 10.0)
        assert abs(ema2 - expected) < 1e-9
        assert n2 == 2

    def test_constant_series_converges_to_price(self):
        ema, n = 0.0, 0
        for _ in range(100):
            ema, n = _ema_step(ema, n, 5.0, 9)
        assert abs(ema - 5.0) < 0.001

    @pytest.mark.parametrize("period", [9, 12, 20, 26])
    def test_k_coefficient_per_period(self, period):
        ema1, n1 = _ema_step(0.0, 0, 10.0, period)
        ema2, _  = _ema_step(ema1, n1, 20.0, period)
        expected = 10.0 + (2.0 / (period + 1)) * (20.0 - 10.0)
        assert abs(ema2 - expected) < 1e-9

    def test_n_increments_each_call(self):
        _, n1 = _ema_step(0.0, 0, 1.0, 9)
        _, n2 = _ema_step(1.0, n1, 1.0, 9)
        _, n3 = _ema_step(1.0, n2, 1.0, 9)
        assert n1 == 1
        assert n2 == 2
        assert n3 == 3


class TestIndicatorRejectReason:
    def test_passes_with_no_history(self):
        assert _indicator_reject_reason(SymbolState(), 10.0) == ""

    # ── VWAP filter ───────────────────────────────────────────────────────────

    def test_rejects_price_equal_to_vwap(self):
        s = SymbolState()
        s.vwap_prev = 10.0
        reason = _indicator_reject_reason(s, 10.0)
        assert "vwap" in reason.lower()

    def test_rejects_price_below_vwap(self):
        s = SymbolState()
        s.vwap_prev = 10.5
        reason = _indicator_reject_reason(s, 10.0)
        assert "vwap" in reason.lower()

    def test_passes_price_above_vwap(self):
        s = SymbolState()
        s.vwap_prev = 9.5
        assert _indicator_reject_reason(s, 10.0) == ""

    def test_vwap_filter_skipped_when_vwap_is_zero(self):
        s = SymbolState()
        s.vwap_prev = 0.0
        assert _indicator_reject_reason(s, 10.0) == ""

    # ── EMA filter ────────────────────────────────────────────────────────────

    def test_rejects_price_below_ema9(self):
        s = SymbolState()
        s.ema9 = 11.0;  s.ema9_n  = 3
        s.ema20 = 9.0;  s.ema20_n = 3
        reason = _indicator_reject_reason(s, 10.0)
        assert "ema9" in reason.lower()

    def test_rejects_bearish_ema_alignment(self):
        s = SymbolState()
        s.ema9 = 9.5;   s.ema9_n  = 3
        s.ema20 = 10.0; s.ema20_n = 3
        reason = _indicator_reject_reason(s, 11.0)
        assert "ema9" in reason.lower() or "ema20" in reason.lower()

    def test_ema_filter_skipped_when_not_warmed_up(self):
        s = SymbolState()
        s.ema9 = 11.0;  s.ema9_n  = 1   # needs ≥ 2
        s.ema20 = 9.0;  s.ema20_n = 1
        assert _indicator_reject_reason(s, 10.0) == ""

    # ── MACD filter ───────────────────────────────────────────────────────────

    def test_rejects_non_positive_macd_hist(self):
        s = SymbolState()
        s.macd_n    = 10
        s.macd_hist = -0.01
        reason = _indicator_reject_reason(s, 10.0)
        assert "macd" in reason.lower()

    def test_rejects_when_no_crossover_prev_positive(self):
        s = SymbolState()
        s.macd_n         = 10
        s.macd_hist      = 0.02
        s.macd_hist_prev = 0.01
        reason = _indicator_reject_reason(s, 10.0)
        assert "macd" in reason.lower()

    def test_passes_fresh_macd_crossover(self):
        s = SymbolState()
        s.macd_n         = 10
        s.macd_hist      = 0.01
        s.macd_hist_prev = -0.005   # just crossed from below
        assert _indicator_reject_reason(s, 10.0) == ""

    def test_macd_filter_skipped_when_not_warmed_up(self):
        s = SymbolState()
        s.macd_n    = 8    # needs ≥ 9
        s.macd_hist = -0.5
        assert _indicator_reject_reason(s, 10.0) == ""

    # ── L2 filter ─────────────────────────────────────────────────────────────

    def test_rejects_bid_depth_less_than_ask_depth(self):
        s = SymbolState()
        s.bids = [(10.0, 100), (9.9, 50)]
        s.asks = [(10.1, 300), (10.2, 100)]
        reason = _indicator_reject_reason(s, 11.0)
        assert "l2" in reason.lower()

    def test_passes_bid_depth_greater_than_ask_depth(self):
        s = SymbolState()
        s.bids = [(10.0, 300), (9.9, 200)]
        s.asks = [(10.1, 100), (10.2,  50)]
        assert _indicator_reject_reason(s, 11.0) == ""

    def test_l2_filter_skipped_when_no_orderbook(self):
        s = SymbolState()
        s.bids = []
        s.asks = []
        assert _indicator_reject_reason(s, 10.0) == ""


class TestLxLimitPriceHelper:
    def test_default_multiplier(self):
        result = _lx(10.0)
        assert abs(result - round(10.0 - _SELL_OFFSET, 2)) < 1e-9

    def test_custom_multiplier(self):
        result = _lx(10.0, 1.5)
        assert abs(result - round(10.0 - _SELL_OFFSET * 1.5, 2)) < 1e-9

    def test_result_is_rounded_to_cents(self):
        result = _lx(10.123, 1.0)
        assert result == round(result, 2)

    def test_watchdog_multiplier(self):
        from execution.position_manager import _WATCHDOG_MULT
        base = _lx(10.0)
        wide = _lx(10.0, _WATCHDOG_MULT)
        assert wide < base   # wider offset means lower limit price
