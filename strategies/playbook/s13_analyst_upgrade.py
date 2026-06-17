from __future__ import annotations

from strategies.playbook.common import make_signal, normalize_bars, prior_close, recent_volume_ratio


def check_signal(symbol: str, bars, context: dict | None = None) -> dict | None:
    context = context or {}
    df = normalize_bars(bars)
    if len(df) < 2:
        return None

    if context.get("analyst_upgrade_age_days", 999) > 2:
        return None
    previous_close = prior_close(df)
    if previous_close is None or float(df["close"].iloc[-1]) <= previous_close:
        return None
    rvol = recent_volume_ratio(df, period=min(20, max(1, len(df) - 1)))
    if rvol is None or rvol < 1.2:
        return None

    return make_signal(
        symbol,
        "S13",
        "analyst_upgrade",
        df,
        strength=2,
        details={"volume_ratio": round(rvol, 3), "previous_close": previous_close},
    )
