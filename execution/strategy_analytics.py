from __future__ import annotations

from collections import defaultdict

from agents.telegram_notifier import send_daily_summary
from execution.trade_logger import load_trade_records

STRATEGY_NAMES = {"ISR": "Institutional Swing Routine"}


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
        winners = [float(t.get("pnl", 0)) for t in trades if float(t.get("pnl", 0)) > 0]
        losers = [abs(float(t.get("pnl", 0))) for t in trades if float(t.get("pnl", 0)) < 0]
        avg_win = round(sum(winners) / len(winners), 2) if winners else 0.0
        avg_loss = round(sum(losers) / len(losers), 2) if losers else 0.0
        rr_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0
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
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "rr_ratio": rr_ratio,
            }
        )
    return rows


def compute_streaks(records: list[dict]) -> dict:
    """Returns max_win_streak, max_loss_streak, current_streak."""
    max_win = max_loss = cur_win = cur_loss = 0
    for r in records:
        if float(r.get("pnl", 0)) > 0:
            cur_win += 1
            cur_loss = 0
            max_win = max(max_win, cur_win)
        else:
            cur_loss += 1
            cur_win = 0
            max_loss = max(max_loss, cur_loss)
    current = cur_win if cur_win > cur_loss else -cur_loss
    return {"max_win_streak": max_win, "max_loss_streak": max_loss, "current_streak": current}


def build_catalyst_scoreboard(records: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        grouped[str(r.get("catalyst_type", "unknown"))].append(r)
    rows = []
    for cat, trades in sorted(grouped.items()):
        wins = sum(1 for t in trades if float(t.get("pnl", 0)) > 0)
        total_pnl = round(sum(float(t.get("pnl", 0)) for t in trades), 2)
        rows.append({
            "catalyst_type": cat,
            "trades": len(trades),
            "wins": wins,
            "win_rate": round(wins / len(trades) * 100, 2),
            "total_pnl": total_pnl,
        })
    return rows


def format_scoreboard(rows: list[dict]) -> str:
    if not rows:
        return "No closed swing trades logged yet."
    lines = ["Strategy  Trades  Win%       P&L  R:R  Best/Worst"]
    for row in rows:
        lines.append(
            f"{row['strategy_id']:<8} {row['trades']:>6} {row['win_rate']:>5.1f}% "
            f"P&L={row['total_pnl']:>7.2f} R:R={row['rr_ratio']:>4.2f} "
            f"Best={row['best_trade']}/Worst={row['worst_trade']}"
        )
    return "\n".join(lines)


async def send_strategy_daily_summary() -> None:
    from execution.pnl_tracker import append_equity_curve
    from execution.trade_logger import today_realized_pnl
    records = load_trade_records()
    scoreboard = build_strategy_scoreboard(records)
    today_pnl = today_realized_pnl()
    append_equity_curve(realized_pnl_today=today_pnl, unrealized_pnl=0.0, open_count=0)
    await send_daily_summary(format_scoreboard(scoreboard))


def main() -> None:
    print(format_scoreboard(build_strategy_scoreboard(load_trade_records())))


if __name__ == "__main__":
    main()
