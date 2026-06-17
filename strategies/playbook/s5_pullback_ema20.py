from __future__ import annotations

from strategies.playbook.common import daily_context, make_signal, normalize_bars


def _volume_declining_three_days(df) -> bool:
    if len(df) < 3:
        return False
    vols = [float(v) for v in df["volume"].tail(3)]
    return vols[0] > vols[1] > vols[2]


def check_signal(symbol: str, bars, context: dict | None = None) -> dict | None:
    context = context or {}
    df = normalize_bars(bars)
    if len(df) < 50:
        return None

    values = daily_context(df)
    close = float(df["close"].iloc[-1])
    low = float(df["low"].iloc[-1])
    ema20 = values["ema20"]
    ema50 = values["ema50"]

    if ema20 is None or ema50 is None:
        return None
    if close <= ema50:
        return None
    if abs(low - ema20) / ema20 > 0.02:
        return None
    if not _volume_declining_three_days(df):
        return None

    return make_signal(
        symbol,
        "S5",
        context.get("catalyst_type", "technical_breakout"),
        df,
        details={"ema20": round(ema20, 4), "ema50": round(ema50, 4)},
    )
