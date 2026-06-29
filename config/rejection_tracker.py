from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
SWING_STAGES = ("universe", "baseline", "eod_scan", "premarket", "entry", "position_manager", "news")


class _RejectionTracker:
    def __init__(self) -> None:
        self._data: dict[str, list[dict]] = defaultdict(list)
        self._seen: set[tuple[str, str]] = set()
        self._watchlisted: set[str] = set()
        self._confirmed: set[str] = set()
        self._traded: set[str] = set()
        self._lock = threading.Lock()

    def record(self, symbol: str, stage: str, reason: str, **details) -> None:
        key = (symbol, stage)
        with self._lock:
            if key in self._seen:
                return
            self._seen.add(key)
            entry = {
                "time": datetime.now(_ET).strftime("%Y-%m-%d %H:%M:%S"),
                "stage": stage,
                "reason": reason,
            }
            entry.update({key: value for key, value in details.items() if value is not None})
            self._data[symbol].append(entry)

    def record_watchlist_entry(self, symbol: str) -> None:
        with self._lock:
            self._watchlisted.add(symbol)

    def record_confirmed(self, symbol: str) -> None:
        with self._lock:
            self._confirmed.add(symbol)

    def record_traded(self, symbol: str) -> None:
        with self._lock:
            self._traded.add(symbol)

    def get_report(self) -> dict:
        with self._lock:
            symbols = {symbol: list(entries) for symbol, entries in self._data.items()}
            summary = {
                "universe_rejected": self._count_stage(symbols, "universe"),
                "baseline_rejected": self._count_stage(symbols, "baseline"),
                "eod_scan_rejected": self._count_stage(symbols, "eod_scan"),
                "premarket_rejected": self._count_stage(symbols, "premarket"),
                "entry_rejected": self._count_stage(symbols, "entry"),
                "news_blocked": sum(
                    1
                    for entries in symbols.values()
                    if any(entry["stage"] == "news" and "block" in entry["reason"] for entry in entries)
                ),
                "watchlisted": len(self._watchlisted),
                "confirmed": len(self._confirmed),
                "traded": len(self._traded),
            }
            traded = sorted(self._traded)
        return {
            "date": datetime.now(_ET).strftime("%Y-%m-%d"),
            "generated_at": datetime.now(_ET).strftime("%H:%M:%S ET"),
            "summary": summary,
            "traded": traded,
            "symbols": symbols,
        }

    @staticmethod
    def _count_stage(symbols: dict[str, list[dict]], stage: str) -> int:
        return sum(1 for entries in symbols.values() if any(entry["stage"] == stage for entry in entries))

    def get_stage_stats(self, stage: str) -> dict:
        with self._lock:
            reasons: dict[str, int] = defaultdict(int)
            for entries in self._data.values():
                for entry in entries:
                    if entry["stage"] == stage:
                        reasons[entry["reason"]] += 1
            return dict(reasons)

    def analyze_top_rejections(self, top_n: int = 5) -> str:
        with self._lock:
            all_reasons: dict[str, int] = defaultdict(int)
            for entries in self._data.values():
                for entry in entries:
                    key = f"{entry['stage']}/{entry['reason']}"
                    all_reasons[key] += 1
        sorted_reasons = sorted(all_reasons.items(), key=lambda x: x[1], reverse=True)
        lines = ["Top rejection reasons:"]
        for reason, count in sorted_reasons[:top_n]:
            lines.append(f"  {reason}: {count}")
        return "\n".join(lines)

    def save_report(self) -> str:
        report = self.get_report()
        report["top_rejections"] = self.get_stage_stats("entry")
        report["rejection_analysis"] = self.analyze_top_rejections()
        os.makedirs("logs", exist_ok=True)
        path = os.path.join("logs", f"rejections_{report['date']}.json")
        with open(path, "w") as f:
            json.dump(report, f, indent=2)
        return path


rejection_tracker = _RejectionTracker()
