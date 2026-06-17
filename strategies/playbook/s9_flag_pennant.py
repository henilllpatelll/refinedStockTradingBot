from __future__ import annotations

from strategies.playbook.common import make_signal, normalize_bars, recent_volume_ratio


def _has_recent_impulse(df) -> bool:
    lookback = df.iloc[:-4] if len(df) > 5 else df.iloc[:2]
    if lookback.empty:
        return False
    closes = list(df["close"])
    for days in (1, 2, 3):
        if len(closes) <= days + 3:
            continue
        start = closes[-(days + 4)]
        end = closes[-4]
        if start > 0 and (end - start) / start >= 0.05:
            return True
    return False


def check_signal(symbol: str, bars, context: dict | None = None) -> dict | None:
    context = context or {}
    df = normalize_bars(bars)
    if len(df) < 6:
        return None

    consolidation = df.iloc[-4:-1]
    cons_high = float(consolidation["high"].max())
    cons_low = float(consolidation["low"].min())
    cons_range_pct = (cons_high - cons_low) / cons_low if cons_low > 0 else 1.0
    close = float(df["close"].iloc[-1])
    rvol = recent_volume_ratio(df, period=min(20, max(2, len(df) - 1)))

    if not _has_recent_impulse(df):
        return None
    if cons_range_pct >= 0.03:
        return None
    if close <= cons_high:
        return None
    if rvol is None or rvol < 2.0:
        return None

    return make_signal(
        symbol,
        "S9",
        context.get("catalyst_type", "technical_breakout"),
        df,
        strength=2,
        details={"consolidation_range_pct": round(cons_range_pct * 100, 2), "volume_ratio": round(rvol, 3)},
    )
