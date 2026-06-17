import pytest

from execution.position_manager import (
    STRATEGY_EXIT_RULES,
    PositionState,
    apply_price_exit_rules,
    build_daily_exit_data,
    calculate_atr_stop,
    load_position_state,
    open_positions,
    pending_entry_orders,
    reconcile_pending_entry_orders,
    save_position_state,
    should_daily_close_exit,
)
from execution.swing_entry import position_budget_for_signal_count, _validate_entry_price
from execution import swing_entry
from strategies.tier1_universe_sweep import parse_finviz_number


@pytest.mark.parametrize(
    "signal_count,expected",
    [(1, 500.0), (2, 750.0), (3, 1000.0), (7, 1000.0)],
)
def test_position_budget_for_signal_count(signal_count, expected):
    assert position_budget_for_signal_count(signal_count) == expected


@pytest.mark.asyncio
async def test_entry_orders_use_latest_price_when_available(monkeypatch):
    submitted = []

    async def fake_prices(symbols):
        assert symbols == ["ABCD"]
        return {"ABCD": 11.0}

    async def fake_submit(setup, shares, limit_price):
        submitted.append((setup, shares, limit_price))
        return "order-1"

    monkeypatch.setattr(swing_entry, "fetch_latest_prices", fake_prices)
    monkeypatch.setattr(swing_entry, "submit_swing_entry", fake_submit)

    # close=10.5 → live price 11.0 is 4.8% above EOD, within the 8% chase threshold
    result = await swing_entry.place_entry_orders(
        [{"symbol": "ABCD", "strategy_id": "S1", "close": 10.5, "details": {}, "signal_count_for_symbol": 1}]
    )

    assert result[0]["entry_order_id"] == "order-1"
    assert submitted[0][1] == 45
    assert submitted[0][2] == 11.0


@pytest.mark.parametrize("strategy_id,details,eod_close,live_price,expected", [
    # Chase filter: live price >8% above EOD close
    ("S1", {}, 10.0, 11.0, False),
    # S1: price below breakout level
    ("S1", {"prior_swing_high": 12.0}, 11.0, 11.5, False),
    # S1: price above breakout level and within chase threshold
    ("S1", {"prior_swing_high": 10.0}, 11.0, 11.5, True),
    # S2: price below 52w high
    ("S2", {"high_52w": 12.0}, 11.0, 11.5, False),
    # S4: price below earnings day low
    ("S4", {"earnings_day_low": 12.0}, 11.0, 11.5, False),
    # S5: price >3% from EMA20
    ("S5", {"ema20": 10.0}, 11.0, 11.5, False),
    # S5: price within 3% of EMA20
    ("S5", {"ema20": 11.3}, 11.0, 11.5, True),
    # S9: price fell >3% below EOD close
    ("S9", {}, 12.0, 11.5, False),
    # S13: price below previous close
    ("S13", {"previous_close": 12.0}, 11.0, 11.5, False),
])
def test_validate_entry_price(strategy_id, details, eod_close, live_price, expected):
    setup = {"symbol": "TEST", "strategy_id": strategy_id, "details": details, "close": eod_close}
    assert _validate_entry_price(setup, live_price) is expected


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


def test_build_daily_exit_data_computes_latest_close_and_ema20():
    import pandas as pd

    bars = pd.DataFrame({"close": [20 + i for i in range(25)]})

    data = build_daily_exit_data({"ABCD": bars})

    assert data["ABCD"]["close"] == 44.0
    assert data["ABCD"]["ema20"] is not None


@pytest.mark.asyncio
async def test_reconcile_pending_entry_orders_registers_filled_order(monkeypatch):
    class FilledOrder:
        status = "filled"
        filled_qty = "25"
        filled_avg_price = "10.50"

    class FakeClient:
        def get_order_by_id(self, order_id):
            assert order_id == "order-1"
            return FilledOrder()

    async def fake_entry_alert(*args, **kwargs):
        return None

    async def fake_stop(position, stop_price):
        return "stop-1"

    pending_entry_orders.clear()
    open_positions.clear()
    pending_entry_orders["order-1"] = {
        "symbol": "ABCD",
        "strategy_id": "S1",
        "catalyst_type": "technical_breakout",
        "shares": 25,
        "limit_price": 10.5,
        "atr_14": 1.0,
    }
    monkeypatch.setattr("execution.position_manager._client", lambda: FakeClient())
    monkeypatch.setattr("execution.position_manager.send_entry_alert", fake_entry_alert)
    monkeypatch.setattr("execution.position_manager.submit_protective_stop", fake_stop)
    monkeypatch.setattr("execution.position_manager.save_position_state", lambda: None)

    filled = await reconcile_pending_entry_orders()

    assert filled == [("ABCD", "S1")]
    assert open_positions[("ABCD", "S1")].entry_price == 10.5
    assert open_positions[("ABCD", "S1")].protective_stop_order_id == "stop-1"
    assert "order-1" not in pending_entry_orders


@pytest.mark.asyncio
async def test_apply_price_exit_rules_takes_t1_and_closes_trailing_runner(monkeypatch):
    submitted = []
    records = []

    async def fake_exit_order(symbol, shares, reason):
        submitted.append((symbol, shares, reason))
        return f"exit-{len(submitted)}"

    async def fake_exit_alert(*args, **kwargs):
        return None

    async def fake_cancel(order_id):
        return True

    async def fake_stop(position, stop_price):
        position.protective_stop_order_id = f"stop-{position.remaining_shares}-{round(stop_price, 2)}"
        return position.protective_stop_order_id

    open_positions.clear()
    position = PositionState(
        symbol="ABCD",
        strategy_id="S1",
        catalyst_type="technical_breakout",
        entry_price=100.0,
        shares=10,
        atr_at_entry=5.0,
        protective_stop_order_id="initial-stop",
    )
    open_positions[("ABCD", "S1")] = position
    monkeypatch.setattr("execution.position_manager.submit_exit_order", fake_exit_order)
    monkeypatch.setattr("execution.position_manager.cancel_order", fake_cancel)
    monkeypatch.setattr("execution.position_manager.submit_protective_stop", fake_stop)
    monkeypatch.setattr("execution.position_manager.send_exit_alert", fake_exit_alert)
    monkeypatch.setattr("execution.position_manager.append_trade_record", lambda record: records.append(record))
    monkeypatch.setattr("execution.position_manager.save_position_state", lambda: None)

    actions = await apply_price_exit_rules(position, 103.0)

    assert actions == ["T1_TARGET"]
    assert position.t1_filled is True
    assert position.remaining_shares == 5
    assert submitted[-1] == ("ABCD", 5, "T1_TARGET")
    assert position.protective_stop_order_id == "stop-5-100.0"

    actions = await apply_price_exit_rules(position, 100.30)

    assert actions == ["TRAILING_STOP"]
    assert ("ABCD", "S1") not in open_positions
    assert submitted[-1] == ("ABCD", 5, "TRAILING_STOP")
    assert records[-1]["pnl"] == pytest.approx(16.5)


def test_position_state_round_trips_to_disk(tmp_path):
    pending_entry_orders.clear()
    open_positions.clear()
    open_positions[("ABCD", "S1")] = PositionState(
        symbol="ABCD",
        strategy_id="S1",
        catalyst_type="technical_breakout",
        entry_price=10.0,
        shares=20,
        atr_at_entry=1.0,
        protective_stop_order_id="stop-1",
    )
    pending_entry_orders["order-1"] = {"symbol": "EFGH", "strategy_id": "S2", "shares": 10}
    path = tmp_path / "position_state.json"

    save_position_state(path)
    open_positions.clear()
    pending_entry_orders.clear()
    load_position_state(path)

    assert open_positions[("ABCD", "S1")].protective_stop_order_id == "stop-1"
    assert pending_entry_orders["order-1"]["symbol"] == "EFGH"


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
