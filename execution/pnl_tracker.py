from __future__ import annotations

import csv
import logging
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_log = logging.getLogger(__name__)
_EQUITY_CURVE_PATH = Path("logs/equity_curve.csv")


def compute_unrealized_pnl(open_positions: dict, prices: dict[str, float]) -> dict[str, float]:
    """Returns {symbol:strategy_id: unrealized_pnl} for all open positions."""
    result: dict[str, float] = {}
    for (symbol, strategy_id), position in open_positions.items():
        price = prices.get(symbol)
        if price is None:
            continue
        shares = position.remaining_shares if position.remaining_shares > 0 else position.shares
        unrealized = (float(price) - position.entry_price) * shares
        result[f"{symbol}:{strategy_id}"] = round(unrealized, 2)
    return result


def total_unrealized_pnl(open_positions: dict, prices: dict[str, float]) -> float:
    return round(sum(compute_unrealized_pnl(open_positions, prices).values()), 2)


def log_position_summary(open_positions: dict, prices: dict[str, float]) -> None:
    if not open_positions:
        _log.info("PnLTracker | no open positions")
        return
    upnl = compute_unrealized_pnl(open_positions, prices)
    total = sum(upnl.values())
    for key, pnl in sorted(upnl.items()):
        symbol, strategy_id = key.split(":", 1)
        pos = open_positions.get((symbol, strategy_id))
        if pos is None:
            continue
        price = prices.get(symbol, pos.entry_price)
        _log.info(
            "PnLTracker | %s %s entry=%.2f current=%.2f upnl=%.2f shares=%d",
            symbol, strategy_id, pos.entry_price, price, pnl,
            pos.remaining_shares if pos.remaining_shares > 0 else pos.shares,
        )
    _log.info("PnLTracker | total unrealized P&L: %.2f", total)


def check_pnl_alerts(
    open_positions: dict,
    prices: dict[str, float],
    max_risk_per_trade: float = 40.0,
) -> list[str]:
    """Returns list of symbols approaching max loss threshold (>75% of max risk)."""
    alerts: list[str] = []
    upnl = compute_unrealized_pnl(open_positions, prices)
    threshold = -max_risk_per_trade * 0.75
    for key, pnl in upnl.items():
        if pnl < threshold:
            alerts.append(f"{key} unrealized={pnl:.2f} (threshold={threshold:.2f})")
            _log.warning("PnLTracker | ALERT %s", alerts[-1])
    return alerts


def append_equity_curve(
    realized_pnl_today: float,
    unrealized_pnl: float,
    open_count: int,
    path: Path = _EQUITY_CURVE_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["date", "realized_pnl", "unrealized_pnl", "total_pnl", "open_positions"])
        writer.writerow([
            date.today().isoformat(),
            round(realized_pnl_today, 2),
            round(unrealized_pnl, 2),
            round(realized_pnl_today + unrealized_pnl, 2),
            open_count,
        ])
