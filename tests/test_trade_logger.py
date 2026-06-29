import json

import pytest

from execution.strategy_analytics import build_strategy_scoreboard
from execution.trade_logger import TradeRecord, append_trade_record


def test_append_trade_record_writes_strategy_aware_json(tmp_path):
    path = tmp_path / "trades_log.json"
    record = TradeRecord(
        symbol="ABCD",
        strategy_id="ISR",
        catalyst_type="technical_breakout",
        entry_price=10.0,
        exit_price=10.5,
        pnl=25.0,
        hold_days=3,
        exit_reason="TRAIL_STOP",
        signal_strength=2,
    )

    append_trade_record(record, path=path)

    data = json.loads(path.read_text())
    assert data[0]["symbol"] == "ABCD"
    assert data[0]["strategy_id"] == "ISR"
    assert data[0]["catalyst_type"] == "technical_breakout"


def test_strategy_scoreboard_groups_win_rate_and_pnl():
    records = [
        {
            "symbol": "ABCD",
            "strategy_id": "ISR",
            "pnl": 25.0,
            "exit_reason": "TRAIL_STOP",
        },
        {
            "symbol": "WXYZ",
            "strategy_id": "ISR",
            "pnl": -10.0,
            "exit_reason": "MAX_LOSS",
        },
        {
            "symbol": "HIGH",
            "strategy_id": "ISR",
            "pnl": 12.0,
            "exit_reason": "T1",
        },
    ]

    scoreboard = build_strategy_scoreboard(records)

    assert scoreboard[0]["strategy_id"] == "ISR"
    assert scoreboard[0]["strategy_name"] == "Institutional Swing Routine"
    assert scoreboard[0]["trades"] == 3
    assert scoreboard[0]["win_rate"] == pytest.approx(66.67)
    assert scoreboard[0]["total_pnl"] == 27.0
