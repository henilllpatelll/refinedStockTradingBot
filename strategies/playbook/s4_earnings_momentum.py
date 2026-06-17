from __future__ import annotations

from strategies.playbook.common import make_signal, normalize_bars


def check_signal(symbol: str, bars, context: dict | None = None) -> dict | None:
    context = context or {}
    df = normalize_bars(bars)
    if len(df) < 2:
        return None

    if context.get("earnings_beat_age_days", 999) > 2:
        return None
    if float(context.get("gap_pct", 0.0)) < 3.0:
        return None

    close = float(df["close"].iloc[-1])
    earnings_day_low = float(context.get("earnings_day_low", df["low"].iloc[-1]))
    if close < earnings_day_low:
        return None

    return make_signal(
        symbol,
        "S4",
        "earnings_beat",
        df,
        strength=2,
        details={"gap_pct": float(context.get("gap_pct", 0.0)), "earnings_day_low": earnings_day_low},
    )
