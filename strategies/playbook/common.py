from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from utils.indicators import atr, ema_last, fifty_two_week_high, volume_sma


STRATEGY_NAMES = {
    "S1": "Breakout",
    "S2": "52-Week High",
    "S4": "Earnings Momentum",
    "S5": "Pullback to EMA20",
    "S9": "Flag/Pennant",
    "S12": "Sector Rotation",
    "S13": "Analyst Upgrade",
}

DEFAULT_HOLD_DAYS = {
    "S1": (2, 5),
    "S2": (2, 5),
    "S4": (3, 7),
    "S5": (3, 8),
    "S9": (4, 10),
    "S12": (5, 14),
    "S13": (3, 7),
}


def normalize_bars(bars: Any) -> pd.DataFrame:
    df = bars if isinstance(bars, pd.DataFrame) else pd.DataFrame(bars)
    if df.empty:
        return df
    return df.rename(columns={col: str(col).lower() for col in df.columns}).reset_index(drop=True)


def last_close(df: pd.DataFrame) -> float:
    return float(df["close"].iloc[-1])


def prior_close(df: pd.DataFrame) -> Optional[float]:
    if len(df) < 2:
        return None
    return float(df["close"].iloc[-2])


def recent_volume_ratio(df: pd.DataFrame, period: int = 20) -> Optional[float]:
    if df.empty or "volume" not in df:
        return None
    avg_volume = volume_sma(df.iloc[:-1], period) or volume_sma(df, period)
    if not avg_volume:
        return None
    return float(df["volume"].iloc[-1]) / avg_volume


def make_signal(
    symbol: str,
    strategy_id: str,
    catalyst_type: str,
    df: pd.DataFrame,
    strength: int = 1,
    details: Optional[dict] = None,
) -> dict:
    atr_value = atr(df, 14)
    return {
        "symbol": symbol,
        "strategy_id": strategy_id,
        "strategy_name": STRATEGY_NAMES[strategy_id],
        "catalyst_type": catalyst_type,
        "signal_strength": strength,
        "hold_days": DEFAULT_HOLD_DAYS[strategy_id],
        "close": round(last_close(df), 4),
        "atr_14": round(atr_value, 4) if atr_value is not None else None,
        "details": details or {},
    }


def daily_context(df: pd.DataFrame) -> dict:
    return {
        "ema20": ema_last(df["close"], 20) if len(df) >= 20 else None,
        "ema50": ema_last(df["close"], 50) if len(df) >= 50 else None,
        "volume_sma20": volume_sma(df, 20),
        "high_52w": fifty_two_week_high(df),
    }
