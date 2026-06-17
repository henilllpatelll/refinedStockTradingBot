from __future__ import annotations

from strategies.playbook.common import daily_context, make_signal, normalize_bars, recent_volume_ratio


def check_signal(symbol: str, bars, context: dict | None = None) -> dict | None:
    context = context or {}
    df = normalize_bars(bars)
    if len(df) < 50:
        return None

    values = daily_context(df)
    close = float(df["close"].iloc[-1])
    latest_high = float(df["high"].iloc[-1])
    high_52w = float(df["high"].iloc[:-1].tail(252).max())
    ema50 = values["ema50"]
    rvol = recent_volume_ratio(df, period=min(20, max(1, len(df) - 1)))

    if latest_high <= high_52w:
        return None
    if rvol is None or rvol <= 1.5:
        return None
    if ema50 is None or close <= ema50:
        return None

    return make_signal(
        symbol,
        "S2",
        context.get("catalyst_type", "technical_breakout"),
        df,
        details={"high_52w": round(high_52w, 4), "volume_ratio": round(rvol, 3)},
    )
