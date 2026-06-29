from __future__ import annotations

from typing import Any

import pandas as pd

from utils.indicators import atr, ema_last, fifty_two_week_high, volume_sma

STRATEGY_ID = "ISR"
STRATEGY_NAME = "Institutional Swing Routine"
HOLD_DAYS = (2, 20)
MIN_RS_RATING = 70
MAX_EXTENSION_FROM_EMA20 = 0.15


def normalize_bars(bars: Any) -> pd.DataFrame:
    df = bars if isinstance(bars, pd.DataFrame) else pd.DataFrame(bars)
    if df.empty:
        return df
    return df.rename(columns={col: str(col).lower() for col in df.columns}).reset_index(drop=True)


def _sma(series: pd.Series, period: int) -> float | None:
    if len(series) < period:
        return None
    return float(series.tail(period).mean())


def _weekly_bars(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.index, pd.DatetimeIndex):
        weekly = df.resample("W-FRI").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        return weekly.dropna(subset=["close"]).reset_index(drop=True)

    rows: list[dict[str, float]] = []
    for start in range(0, len(df), 5):
        chunk = df.iloc[start:start + 5]
        if chunk.empty:
            continue
        rows.append(
            {
                "open": float(chunk["open"].iloc[0]),
                "high": float(chunk["high"].max()),
                "low": float(chunk["low"].min()),
                "close": float(chunk["close"].iloc[-1]),
                "volume": float(chunk["volume"].sum()),
            }
        )
    return pd.DataFrame(rows)


def _weekly_trend_ok(df: pd.DataFrame, context: dict) -> bool:
    weekly = _weekly_bars(df)
    if len(weekly) < 40:
        return False
    close = float(weekly["close"].iloc[-1])
    ma10 = _sma(weekly["close"], 10)
    ma40 = _sma(weekly["close"], 40)
    if ma10 is None or ma40 is None:
        return False
    if not (close > ma10 and close > ma40 and ma10 > ma40):
        return False

    recent = weekly.tail(8)
    prior = weekly.iloc[-16:-8] if len(weekly) >= 16 else weekly.iloc[:-8]
    if prior.empty:
        return False
    higher_high = float(recent["high"].max()) >= float(prior["high"].max())
    higher_low = float(recent["low"].min()) >= float(prior["low"].min())
    near_highs = close >= float(weekly["high"].tail(52).max()) * 0.85
    return higher_high and higher_low and near_highs and _relative_strength_ok(context)


def _relative_strength_ok(context: dict) -> bool:
    rs_rating = context.get("rs_rating")
    if rs_rating is not None and int(rs_rating) < MIN_RS_RATING:
        return False
    stock_rs = context.get("stock_rs")
    sector_rs = context.get("sector_rs")
    if stock_rs is not None and sector_rs is not None and float(stock_rs) <= float(sector_rs):
        return False
    return True


def _theme_ok(context: dict) -> bool:
    catalyst_type = context.get("catalyst_type")
    if catalyst_type and catalyst_type != "technical_breakout":
        return True
    if context.get("theme") or context.get("institutional_theme"):
        return True
    sector_rank = context.get("sector_rank")
    sector_rs = context.get("sector_rs")
    return sector_rank is not None and int(sector_rank) <= 3 and float(sector_rs or 0) > 0


def _sector_ok(context: dict) -> bool:
    sector_rank = context.get("sector_rank")
    sector_rs = context.get("sector_rs")
    if sector_rank is None and sector_rs is None:
        return bool(context.get("theme") or context.get("institutional_theme"))
    return sector_rank is not None and int(sector_rank) <= 3 and float(sector_rs or 0) > 0


def _daily_trend(df: pd.DataFrame) -> dict[str, float] | None:
    if len(df) < 50:
        return None
    close = float(df["close"].iloc[-1])
    ema20 = ema_last(df["close"], 20)
    sma50 = _sma(df["close"], 50)
    avg_volume = volume_sma(df.iloc[:-1], 20) or volume_sma(df, 20)
    if ema20 is None or sma50 is None or avg_volume is None:
        return None
    if close <= ema20 or close <= sma50:
        return None
    if (close - ema20) / ema20 > MAX_EXTENSION_FROM_EMA20:
        return None
    if len(df) >= 2 and close < float(df["close"].iloc[-2]) and float(df["volume"].iloc[-1]) >= avg_volume * 1.5:
        return None
    return {"close": close, "ema20": float(ema20), "sma50": float(sma50), "avg_volume": float(avg_volume)}


def _volume_dried_then_returned(df: pd.DataFrame, avg_volume: float) -> bool:
    if len(df) < 4:
        return False
    pullback_volumes = [float(value) for value in df["volume"].iloc[-4:-1]]
    today_volume = float(df["volume"].iloc[-1])
    return pullback_volumes[0] > pullback_volumes[1] > pullback_volumes[2] and today_volume >= avg_volume * 1.1


def _pullback_reversal(df: pd.DataFrame, daily: dict[str, float]) -> dict | None:
    low = float(df["low"].iloc[-1])
    close = daily["close"]
    prior_close = float(df["close"].iloc[-2])
    support_candidates = [daily["ema20"], daily["sma50"]]
    touched_support = any(abs(low - level) / level <= 0.03 for level in support_candidates)
    if touched_support and close > prior_close and _volume_dried_then_returned(df, daily["avg_volume"]):
        return {
            "setup_type": "pullback_reversal",
            "support_level": round(min(support_candidates, key=lambda level: abs(low - level)), 4),
            "entry_trigger": "reclaim_vwap_or_break_65m_range",
        }
    return None


def _tight_consolidation_breakout(df: pd.DataFrame, daily: dict[str, float]) -> dict | None:
    if len(df) < 11:
        return None
    base = df.iloc[-7:-1]
    range_high = float(base["high"].max())
    range_low = float(base["low"].min())
    if range_low <= 0:
        return None
    tight_range = (range_high - range_low) / range_low <= 0.04
    volume_contracts = float(base["volume"].mean()) < daily["avg_volume"] * 0.8
    volume_expands = float(df["volume"].iloc[-1]) >= daily["avg_volume"] * 1.25
    if tight_range and volume_contracts and daily["close"] > range_high and volume_expands:
        return {
            "setup_type": "tight_consolidation_breakout",
            "range_high": round(range_high, 4),
            "entry_trigger": "break_65m_range_with_vwap_hold",
        }
    return None


def _earnings_gap_and_hold(df: pd.DataFrame, context: dict) -> dict | None:
    if context.get("catalyst_type") != "earnings_beat":
        return None
    if context.get("earnings_beat_age_days", 999) > 2:
        return None
    if float(context.get("gap_pct", 0.0)) < 3.0:
        return None
    earnings_day_low = float(context.get("earnings_day_low", df["low"].iloc[-1]))
    if float(df["close"].iloc[-1]) <= earnings_day_low:
        return None
    return {
        "setup_type": "earnings_gap_and_hold",
        "earnings_day_low": round(earnings_day_low, 4),
        "entry_trigger": "gap_hold_flag_then_volume_break",
    }


def _new_high_breakout(df: pd.DataFrame, daily: dict[str, float]) -> dict | None:
    high_52w = fifty_two_week_high(df)
    if high_52w is None:
        return None
    prior_high = float(df["high"].iloc[:-1].tail(252).max()) if len(df) > 1 else high_52w
    closes_strong = daily["close"] >= float(df["low"].iloc[-1]) + (float(df["high"].iloc[-1]) - float(df["low"].iloc[-1])) * 0.65
    volume_expands = float(df["volume"].iloc[-1]) >= daily["avg_volume"] * 1.5
    if daily["close"] >= prior_high and volume_expands and closes_strong:
        return {
            "setup_type": "new_high_breakout",
            "prior_high": round(prior_high, 4),
            "high_52w": round(float(high_52w), 4),
            "entry_trigger": "hold_breakout_and_close_strong",
        }
    return None


def _make_signal(symbol: str, df: pd.DataFrame, context: dict, daily: dict[str, float], details: dict) -> dict:
    atr_value = atr(df, 14)
    catalyst_type = context.get("catalyst_type")
    if not catalyst_type or catalyst_type == "technical_breakout":
        catalyst_type = "sector_tailwind" if context.get("sector_rank") else "technical_breakout"
    return {
        "symbol": symbol,
        "strategy_id": STRATEGY_ID,
        "strategy_name": STRATEGY_NAME,
        "catalyst_type": catalyst_type,
        "signal_strength": 2 if details["setup_type"] in {"earnings_gap_and_hold", "new_high_breakout"} else 1,
        "hold_days": HOLD_DAYS,
        "close": round(daily["close"], 4),
        "atr_14": round(atr_value, 4) if atr_value is not None else None,
        "details": {
            **details,
            "ema20": round(daily["ema20"], 4),
            "sma50": round(daily["sma50"], 4),
            "timeframe_workflow": "weekly_daily_65m",
        },
    }


def check_signal(symbol: str, bars: Any, context: dict | None = None) -> dict | None:
    context = context or {}
    if context.get("market_regime") == "DOWNTREND":
        return None
    df = normalize_bars(bars)
    if df.empty or not {"open", "high", "low", "close", "volume"}.issubset(df.columns):
        return None
    if not (_theme_ok(context) and _sector_ok(context) and _weekly_trend_ok(df, context)):
        return None
    daily = _daily_trend(df)
    if daily is None:
        return None

    for detector in (
        lambda: _earnings_gap_and_hold(df, context),
        lambda: _pullback_reversal(df, daily),
        lambda: _tight_consolidation_breakout(df, daily),
        lambda: _new_high_breakout(df, daily),
    ):
        details = detector()
        if details is not None:
            return _make_signal(symbol, df, context, daily, details)
    return None
