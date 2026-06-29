from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
DEFAULT_TRADE_LOG_PATH = Path("logs/trades_log.json")


@dataclass(frozen=True)
class TradeRecord:
    symbol: str
    strategy_id: str
    catalyst_type: str
    entry_price: float
    exit_price: float
    pnl: float
    hold_days: int
    exit_reason: str
    signal_strength: int
    entry_time: str | None = None
    exit_time: str | None = None
    slippage_pct: float = 0.0


def _record_to_dict(record: TradeRecord | dict) -> dict:
    data = asdict(record) if isinstance(record, TradeRecord) else dict(record)
    now = datetime.now(_ET).strftime("%Y-%m-%d %H:%M:%S")
    data.setdefault("entry_time", now)
    data.setdefault("exit_time", now)
    data["pnl"] = round(float(data.get("pnl", 0.0)), 2)
    entry = float(data.get("entry_price", 0))
    limit = float(data.get("limit_price", entry))
    if entry > 0 and limit > 0:
        data.setdefault("slippage_pct", round((entry - limit) / limit * 100, 3))
    return data


def load_trade_records(path: str | Path = DEFAULT_TRADE_LOG_PATH) -> list[dict]:
    source = Path(path)
    if not source.exists() or source.stat().st_size == 0:
        return []
    return json.loads(source.read_text())


def append_trade_record(record: TradeRecord | dict, path: str | Path = DEFAULT_TRADE_LOG_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    records = load_trade_records(target)
    records.append(_record_to_dict(record))
    target.write_text(json.dumps(records, indent=2))
    return target


def today_realized_pnl(path: str | Path = DEFAULT_TRADE_LOG_PATH) -> float:
    from datetime import date
    today = date.today().isoformat()
    records = load_trade_records(path)
    return round(sum(float(r.get("pnl", 0)) for r in records if str(r.get("exit_time", "")).startswith(today)), 2)
