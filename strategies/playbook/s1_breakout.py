from __future__ import annotations

from strategies.playbook.common import make_signal, normalize_bars, recent_volume_ratio


def check_signal(symbol: str, bars, context: dict | None = None) -> dict | None:
    context = context or {}
    df = normalize_bars(bars)
    if len(df) < 5:
        return None

    close = float(df["close"].iloc[-1])
    prior_swing_high = float(df["high"].iloc[:-1].tail(20).max())
    rvol = recent_volume_ratio(df, period=min(20, max(1, len(df) - 1)))
    catalyst_age = context.get("catalyst_age_days")

    if close <= prior_swing_high:
        return None
    if rvol is None or rvol < 1.5:
        return None
    if catalyst_age is None or catalyst_age > 3:
        return None

    return make_signal(
        symbol,
        "S1",
        context.get("catalyst_type", "technical_breakout"),
        df,
        strength=1,
        details={"prior_swing_high": round(prior_swing_high, 4), "rvol": round(rvol, 3)},
    )
