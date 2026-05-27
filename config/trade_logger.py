"""
Daily trade logger — records full lifecycle of every trade attempt.

Writes logs/trades_YYYY-MM-DD.json at session end.
Call save_report() from main.py after all tasks complete.
"""

import json
import os
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def _now() -> str:
    return datetime.now(_ET).strftime("%Y-%m-%d %H:%M:%S")


class _TradeLogger:
    def __init__(self) -> None:
        self._completed: list[dict] = []
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    def on_entry_submitted(
        self,
        symbol: str,
        shares: int,
        limit_px: float,
        cvd: float,
        chg_pct: float,
        volume: int,
    ) -> None:
        with self._lock:
            self._pending[symbol] = {
                "symbol": symbol,
                "submitted_time": _now(),
                "shares_requested": shares,
                "entry_limit_px": limit_px,
                "cvd_at_signal": round(cvd, 0),
                "gap_pct": round(chg_pct, 2),
                "volume_at_signal": volume,
            }

    def on_entry_filled(
        self,
        symbol: str,
        fill_px: float,
        fill_qty: int,
        t1_shares: int,
        runner_shares: int,
        stop_price: float,
        rvol: float,
    ) -> None:
        with self._lock:
            rec = self._pending.get(symbol, {"symbol": symbol})
            rec.update({
                "entry_fill_time": _now(),
                "entry_price": fill_px,
                "shares_filled": fill_qty,
                "t1_shares": t1_shares,
                "runner_shares": runner_shares,
                "stop_price": round(stop_price, 4),
                "t1_target": round(fill_px * 1.04, 2),
                "rvol_20": rvol,
            })
            self._pending[symbol] = rec

    def on_t1_filled(self, symbol: str, fill_px: float) -> None:
        with self._lock:
            rec = self._pending.get(symbol)
            if rec:
                rec["t1_fill_price"] = fill_px
                rec["t1_fill_time"] = _now()

    def on_closed(self, symbol: str, exit_px: float, reason: str, pnl: float) -> None:
        with self._lock:
            rec = self._pending.pop(symbol, {"symbol": symbol})
            rec.update({
                "exit_price": exit_px,
                "exit_time": _now(),
                "exit_reason": reason,
                "pnl": round(pnl, 2),
                "outcome": "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven",
            })
            self._completed.append(rec)

    def on_entry_timeout(self, symbol: str) -> None:
        with self._lock:
            rec = self._pending.pop(symbol, None)
            if rec:
                rec["exit_reason"] = "entry_timeout"
                rec["exit_time"] = _now()
                self._completed.append(rec)

    def get_report(self) -> dict:
        with self._lock:
            completed = list(self._completed)
            pending = list(self._pending.values())
            wins   = sum(1 for t in completed if t.get("outcome") == "win")
            losses = sum(1 for t in completed if t.get("outcome") == "loss")
            total_pnl = sum(t.get("pnl", 0.0) for t in completed)
        return {
            "date": datetime.now(_ET).strftime("%Y-%m-%d"),
            "generated_at": datetime.now(_ET).strftime("%H:%M:%S ET"),
            "summary": {
                "total_trades": len(completed),
                "wins": wins,
                "losses": losses,
                "total_pnl": round(total_pnl, 2),
            },
            "trades": completed,
            "open_at_shutdown": pending,
        }

    def save_report(self) -> str:
        report = self.get_report()
        os.makedirs("logs", exist_ok=True)
        path = os.path.join("logs", f"trades_{report['date']}.json")
        with open(path, "w") as f:
            json.dump(report, f, indent=2)
        return path


trade_logger = _TradeLogger()
