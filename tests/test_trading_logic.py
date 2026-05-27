"""
Unit tests for core trading logic.
Covers: RVOL calculation, Lee-Ready tick direction, ORB tracking, entry trigger,
        VWAP/sigma math, exit P&L calculation, and Finviz number parsing.
"""
import math

import pytest

from strategies.trackA_volume_scout import _calc_rvol_20
from execution.trackB_realtime import (
    _tick_direction,
    _process_trade,
    SymbolState,
)
from execution.position_manager import _vwap_sigma, _calc_exit_pnl, _PositionState
from strategies.tier1_universe_sweep import parse_finviz_number


# ── RVOL ──────────────────────────────────────────────────────────────────────

class TestCalcRvol20:
    def test_normal(self):
        result = _calc_rvol_20(current_vol=20_000, sma_vol=100_000.0, elapsed_mins=96.0)
        expected = 20_000 / (100_000 * 96 / 390)
        assert abs(result - expected) < 1e-9

    def test_zero_elapsed(self):
        assert _calc_rvol_20(10_000, 50_000.0, 0.0) == 0.0

    def test_zero_sma(self):
        assert _calc_rvol_20(10_000, 0.0, 30.0) == 0.0

    def test_full_session(self):
        # At elapsed=390 (end of regular session), expected_vol == sma_vol → RVOL = current/sma
        result = _calc_rvol_20(current_vol=50_000, sma_vol=100_000.0, elapsed_mins=390.0)
        assert abs(result - 0.5) < 1e-9


# ── Lee-Ready tick direction ───────────────────────────────────────────────────

class TestTickDirection:
    def test_uptick(self):
        assert _tick_direction(10.50, 10.00, 0) == +1

    def test_downtick(self):
        assert _tick_direction(9.50, 10.00, 0) == -1

    def test_zero_tick_inherits_up(self):
        assert _tick_direction(10.00, 10.00, +1) == +1

    def test_zero_tick_inherits_down(self):
        assert _tick_direction(10.00, 10.00, -1) == -1

    def test_first_tick_indeterminate(self):
        assert _tick_direction(10.00, 0.0, 0) == 0


# ── Entry trigger ─────────────────────────────────────────────────────────────

class TestEntryTrigger:
    def _setup_state(self) -> dict[str, SymbolState]:
        s = SymbolState()
        s.last_price = 10.0   # prior tick so direction is deterministic
        return {"TSLA": s}

    def test_fires_on_uptick_with_positive_cvd(self):
        states = self._setup_state()
        baselines = {"TSLA": {"previous_close": 9.5}}

        signal = _process_trade(
            {"T": "t", "S": "TSLA", "p": 10.50, "s": 500},
            states,
            baselines,
        )

        assert signal is not None
        assert signal["symbol"] == "TSLA"
        assert signal["price"]  == 10.50

    def test_no_signal_on_downtick(self):
        states = self._setup_state()
        baselines = {"TSLA": {"previous_close": 9.5}}

        signal = _process_trade(
            {"T": "t", "S": "TSLA", "p": 9.80, "s": 500},
            states,
            baselines,
        )

        assert signal is None

    def test_fires_on_each_qualifying_tick(self):
        # Entry deduplication is now handled by _receive_loop checking _pm._positions,
        # not inside _process_trade. Two successive upticks above VWAP both signal.
        states = self._setup_state()
        baselines = {"TSLA": {"previous_close": 9.5}}

        first  = _process_trade({"T": "t", "S": "TSLA", "p": 10.50, "s": 500}, states, baselines)
        # Second tick at a higher price keeps price > VWAP so all filters pass again.
        second = _process_trade({"T": "t", "S": "TSLA", "p": 10.60, "s": 500}, states, baselines)

        assert first  is not None
        assert second is not None

    def test_no_signal_on_negative_cvd(self):
        states = self._setup_state()
        # Seed negative CVD: large sell tick first
        states["TSLA"].cvd = -1000.0
        baselines = {"TSLA": {"previous_close": 9.5}}

        signal = _process_trade(
            {"T": "t", "S": "TSLA", "p": 10.50, "s": 1},
            states,
            baselines,
        )

        assert signal is None


# ── VWAP / sigma ──────────────────────────────────────────────────────────────

class TestVwapSigma:
    def _make_state(self, trades: list[tuple[float, int]]) -> _PositionState:
        s = _PositionState(symbol="X", entry_order_id="oid")
        for price, size in trades:
            s._pv  += price * size
            s._v   += size
            s._p2v += price * price * size
        return s

    def test_empty_state(self):
        s = _PositionState(symbol="X", entry_order_id="oid")
        vwap, sigma = _vwap_sigma(s)
        assert vwap == 0.0 and sigma == 0.0

    def test_single_price(self):
        s = self._make_state([(10.0, 100), (10.0, 200)])
        vwap, sigma = _vwap_sigma(s)
        assert abs(vwap - 10.0) < 1e-9
        assert sigma == 0.0

    def test_known_values(self):
        # Two trades: 100sh @ $10, 100sh @ $12 → VWAP=11, sigma=1
        s = self._make_state([(10.0, 100), (12.0, 100)])
        vwap, sigma = _vwap_sigma(s)
        assert abs(vwap - 11.0) < 1e-9
        assert abs(sigma - 1.0) < 1e-9


# ── Exit P&L ──────────────────────────────────────────────────────────────────

class TestCalcExitPnl:
    def _make_pos(self, entry=10.0, t1_shares=25, runner_shares=25,
                  t1_filled=False, t1_fill_price=0.0) -> _PositionState:
        s = _PositionState(symbol="X", entry_order_id="oid")
        s.entry_price   = entry
        s.t1_shares     = t1_shares
        s.runner_shares = runner_shares
        s.t1_filled     = t1_filled
        s.t1_fill_price = t1_fill_price
        return s

    def test_emergency_no_t1(self):
        s = self._make_pos(entry=10.0, t1_shares=25, runner_shares=25,
                           t1_filled=False)
        pnl = _calc_exit_pnl(s, exit_price=9.50)
        assert abs(pnl - (-0.50 * 50)) < 1e-9

    def test_runner_after_t1(self):
        s = self._make_pos(entry=10.0, t1_shares=25, runner_shares=25,
                           t1_filled=True, t1_fill_price=10.40)
        pnl = _calc_exit_pnl(s, exit_price=10.20)
        t1_gain     = (10.40 - 10.0) * 25
        runner_gain = (10.20 - 10.0) * 25
        assert abs(pnl - (t1_gain + runner_gain)) < 1e-9


# ── Finviz number parsing ─────────────────────────────────────────────────────

class TestParseFinvizNumber:
    @pytest.mark.parametrize("raw,expected", [
        ("15.4M",  15_400_000),
        ("850K",   850_000),
        ("1.2B",   1_200_000_000),
        ("500",    500),
        ("-",      None),
        ("N/A",    None),
        ("",       None),
        ("2.5T",   2_500_000_000_000),
    ])
    def test_parse(self, raw, expected):
        assert parse_finviz_number(raw) == expected
