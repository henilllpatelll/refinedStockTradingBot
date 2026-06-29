from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from utils.market_data import fetch_daily_bars

_log = logging.getLogger(__name__)
_DATA_PATH = Path("backtest/data")


def _cache_path(symbol: str, start: date, end: date) -> Path:
    return _DATA_PATH / f"{symbol}_{start}_{end}.parquet"


async def load_bars(
    symbols: list[str],
    start_date: date,
    end_date: date,
    *,
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    """Load daily bars for symbols over the given date range."""
    result: dict[str, pd.DataFrame] = {}
    to_fetch: list[str] = []

    for symbol in symbols:
        cache = _cache_path(symbol, start_date, end_date)
        if use_cache and cache.exists():
            try:
                result[symbol] = pd.read_parquet(cache)
                continue
            except Exception:
                pass
        to_fetch.append(symbol)

    if to_fetch:
        lookback_days = (end_date - start_date).days + 10
        fetched = await fetch_daily_bars(to_fetch, lookback_days=lookback_days, limit=lookback_days)
        for symbol, df in fetched.items():
            if df.empty:
                continue
            if hasattr(df.index, "date"):
                mask = (df.index.date >= start_date) & (df.index.date <= end_date)
                df = df[mask]
            if use_cache and not df.empty:
                cache = _cache_path(symbol, start_date, end_date)
                cache.parent.mkdir(parents=True, exist_ok=True)
                try:
                    df.to_parquet(cache)
                except Exception:
                    pass
            result[symbol] = df
        _log.info("BarLoader | fetched %d/%d symbols", len(fetched), len(to_fetch))

    return result


def slice_bars_up_to(df: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    """Return bars up to and including as_of_date."""
    if df.empty:
        return df
    if hasattr(df.index, "date"):
        return df[df.index.date <= as_of_date]
    return df
