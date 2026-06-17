from __future__ import annotations

from collections import defaultdict

from agents.telegram_notifier import send_daily_summary
from execution.trade_logger import load_trade_records
from strategies.playbook.common import STRATEGY_NAMES


def build_strategy_scoreboard(records: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        grouped[str(record.get("strategy_id", "UNKNOWN"))].append(record)

    rows: list[dict] = []
    for strategy_id in sorted(grouped):
        trades = grouped[strategy_id]
        wins = sum(1 for trade in trades if float(trade.get("pnl", 0.0)) > 0)
        total_pnl = round(sum(float(trade.get("pnl", 0.0)) for trade in trades), 2)
        best = max(trades, key=lambda trade: float(trade.get("pnl", 0.0)))
        worst = min(trades, key=lambda trade: float(trade.get("pnl", 0.0)))
        rows.append(
            {
                "strategy_id": strategy_id,
                "strategy_name": STRATEGY_NAMES.get(strategy_id, strategy_id),
                "trades": len(trades),
                "wins": wins,
                "win_rate": round(wins / len(trades) * 100, 2) if trades else 0.0,
                "total_pnl": total_pnl,
                "best_trade": best.get("symbol"),
                "worst_trade": worst.get("symbol"),
            }
        )
    return rows


def format_scoreboard(rows: list[dict]) -> str:
    if not rows:
        return "No closed swing trades logged yet."
    lines = ["Strategy  Trades  Win%  P&L  Best/Worst"]
    for row in rows:
        lines.append(
            f"{row['strategy_id']:<8} {row['trades']:>6} "
            f"{row['win_rate']:>5.1f} {row['total_pnl']:>7.2f} "
            f"{row['best_trade']}/{row['worst_trade']}"
        )
    return "\n".join(lines)


async def send_strategy_daily_summary() -> None:
    await send_daily_summary(format_scoreboard(build_strategy_scoreboard(load_trade_records())))


def main() -> None:
    print(format_scoreboard(build_strategy_scoreboard(load_trade_records())))


if __name__ == "__main__":
    main()
