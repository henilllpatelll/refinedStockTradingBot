from __future__ import annotations

from strategies.playbook.common import daily_context, make_signal, normalize_bars


def check_signal(symbol: str, bars, context: dict | None = None) -> dict | None:
    context = context or {}
    df = normalize_bars(bars)
    if len(df) < 20:
        return None

    sector_rank = int(context.get("sector_rank", 99))
    stock_rs = float(context.get("stock_rs", 0.0))
    sector_rs = float(context.get("sector_rs", 0.0))
    ema20 = daily_context(df)["ema20"]
    close = float(df["close"].iloc[-1])

    if sector_rank > 3:
        return None
    if stock_rs <= sector_rs:
        return None
    if ema20 is None or close <= ema20:
        return None

    return make_signal(
        symbol,
        "S12",
        "sector_tailwind",
        df,
        strength=2,
        details={"sector_rank": sector_rank, "stock_rs": stock_rs, "sector_rs": sector_rs},
    )
