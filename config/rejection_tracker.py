"""
Daily rejection tracker — records why each symbol was not traded.

Writes logs/rejections_YYYY-MM-DD.json at session end.
Call save_report() from main.py after all tasks complete.
"""

import json
import os
import threading
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


class _RejectionTracker:
    def __init__(self) -> None:
        self._data: dict[str, list[dict]] = defaultdict(list)
        self._seen: set[tuple[str, str]] = set()       # (symbol, stage) dedup
        self._best_rvol: dict[str, float] = {}          # highest RVOL seen per symbol
        self._rvol_idx: dict[str, int] = {}             # cached index of RVOL record
        self._reached_watchlist: set[str] = set()
        self._traded: set[str] = set()
        self._lock = threading.Lock()

    def record(self, symbol: str, stage: str, reason: str, **details) -> None:
        """Record a rejection reason — silently deduplicates per (symbol, stage)."""
        key = (symbol, stage)
        with self._lock:
            if key in self._seen:
                return
            self._seen.add(key)
            entry: dict = {
                "time": datetime.now(_ET).strftime("%Y-%m-%d %H:%M:%S"),
                "stage": stage,
                "reason": reason,
            }
            entry.update({k: v for k, v in details.items() if v is not None})
            self._data[symbol].append(entry)

    def record_rvol(self, symbol: str, rvol: float, threshold: float) -> None:
        """Update best RVOL seen for a sub-threshold symbol (O(1), lock-free fast path)."""
        prev = self._best_rvol.get(symbol, -1.0)
        if rvol <= prev:
            return
        with self._lock:
            prev = self._best_rvol.get(symbol, -1.0)
            if rvol <= prev:
                return
            self._best_rvol[symbol] = rvol
            idx = self._rvol_idx.get(symbol)
            now = datetime.now(_ET).strftime("%Y-%m-%d %H:%M:%S")
            if idx is not None and idx < len(self._data[symbol]):
                self._data[symbol][idx]["best_rvol"] = round(rvol, 3)
                self._data[symbol][idx]["time"] = now
            else:
                new_entry = {
                    "time": now,
                    "stage": "track_a",
                    "reason": "rvol_below_threshold",
                    "best_rvol": round(rvol, 3),
                    "threshold": threshold,
                }
                self._data[symbol].append(new_entry)
                self._rvol_idx[symbol] = len(self._data[symbol]) - 1

    def record_watchlist_entry(self, symbol: str) -> None:
        with self._lock:
            self._reached_watchlist.add(symbol)

    def record_traded(self, symbol: str) -> None:
        with self._lock:
            self._traded.add(symbol)

    def get_report(self) -> dict:
        with self._lock:
            symbols = {k: list(v) for k, v in self._data.items()}
            summary = {
                "tier2_no_baseline": sum(
                    1 for v in symbols.values()
                    if any(e["stage"] == "tier2" for e in v)
                ),
                "news_blocked": sum(
                    1 for v in symbols.values()
                    if any(e["stage"].startswith("news") for e in v)
                ),
                "rvol_too_low": sum(
                    1 for v in symbols.values()
                    if any(e["stage"] == "track_a" and e.get("reason") == "rvol_below_threshold" for e in v)
                ),
                "no_snapshot_data": sum(
                    1 for v in symbols.values()
                    if any(e["stage"] == "track_a" and e.get("reason") == "no_snapshot_data" for e in v)
                ),
                "reached_watchlist": len(self._reached_watchlist),
                "above_threshold_not_top_n": sum(
                    1 for v in symbols.values()
                    if any(e["stage"] == "track_a" and e.get("reason") == "above_threshold_not_top_n" for e in v)
                ),
                "indicator_filtered": sum(
                    1 for v in symbols.values()
                    if any(e["stage"] == "track_b_indicator" for e in v)
                ),
                "gap_too_low": sum(
                    1 for v in symbols.values()
                    if any(e["stage"] == "track_b" and e.get("reason") == "gap_below_threshold" for e in v)
                ),
                "cvd_never_positive": sum(
                    1 for v in symbols.values()
                    if any(e["stage"] == "track_b" and e.get("reason") == "cvd_nonpositive" for e in v)
                ),
                "traded": len(self._traded),
            }
        return {
            "date": datetime.now(_ET).strftime("%Y-%m-%d"),
            "generated_at": datetime.now(_ET).strftime("%H:%M:%S ET"),
            "summary": summary,
            "traded": sorted(self._traded),
            "symbols": symbols,
        }

    def save_report(self) -> str:
        report = self.get_report()
        os.makedirs("logs", exist_ok=True)
        path = os.path.join("logs", f"rejections_{report['date']}.json")
        with open(path, "w") as f:
            json.dump(report, f, indent=2)
        return path


rejection_tracker = _RejectionTracker()
