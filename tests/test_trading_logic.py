import pytest

from execution.position_manager import (
    STRATEGY_EXIT_RULES,
    PositionState,
    calculate_atr_stop,
    should_daily_close_exit,
)
from execution.swing_entry import position_budget_for_signal_count
from strategies.tier1_universe_sweep import parse_finviz_number


@pytest.mark.parametrize(
    "signal_count,expected",
    [(1, 500.0), (2, 750.0), (3, 1000.0), (7, 1000.0)],
)
def test_position_budget_for_signal_count(signal_count, expected):
    assert position_budget_for_signal_count(signal_count) == expected


def test_strategy_exit_rules_are_plan_values():
    assert STRATEGY_EXIT_RULES["S1"].t1_target_pct == 0.03
    assert STRATEGY_EXIT_RULES["S4"].trail_stop_pct == 0.04
    assert STRATEGY_EXIT_RULES["S12"].t1_target_pct == 0.05


def test_atr_stop_is_one_and_half_atr_below_entry():
    assert calculate_atr_stop(entry_price=20.0, atr_value=2.0) == 17.0


def test_daily_close_exit_triggers_below_ema20():
    position = PositionState(
        symbol="ABCD",
        strategy_id="S1",
        catalyst_type="technical_breakout",
        entry_price=20.0,
        shares=25,
        atr_at_entry=1.0,
    )

    assert should_daily_close_exit(position, daily_close=18.5, ema20=19.0) is True
    assert should_daily_close_exit(position, daily_close=19.5, ema20=19.0) is False


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("15.4M", 15_400_000),
        ("850K", 850_000),
        ("1.2B", 1_200_000_000),
        ("500", 500),
        ("-", None),
        ("N/A", None),
        ("2.5T", 2_500_000_000_000),
    ],
)
def test_parse_finviz_number(raw, expected):
    assert parse_finviz_number(raw) == expected
