from __future__ import annotations
from typing import Optional
import pandas as pd


def session_vwap(bars: pd.DataFrame) -> Optional[float]:
    """VWAP from session bars (each row has open/high/low/close/volume)."""
    if bars.empty or "volume" not in bars or "close" not in bars:
        return None
    df = bars.copy()
    df["typical"] = (df["high"] + df["low"] + df["close"]) / 3.0
    total_vol = float(df["volume"].sum())
    if total_vol == 0:
        return None
    return float((df["typical"] * df["volume"]).sum() / total_vol)


def price_to_vwap_sigma(price: float, vwap: float, bars: pd.DataFrame) -> Optional[float]:
    """How many sigma above/below VWAP is the given price?"""
    if bars.empty or vwap == 0:
        return None
    df = bars.copy()
    df["typical"] = (df["high"] + df["low"] + df["close"]) / 3.0
    std = float(df["typical"].std())
    if std == 0:
        return None
    return (price - vwap) / std
