from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from alpaca.data.enums import Adjustment
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame

from config.settings import ALPACA_API_KEY, ALPACA_SECRET_KEY

_ET = ZoneInfo("America/New_York")

DEFAULT_LOOKBACK_DAYS = 420
DEFAULT_BAR_LIMIT = 280
DEFAULT_MAX_CONCURRENT = 10


def _client(client: StockHistoricalDataClient | None = None) -> StockHistoricalDataClient:
    return client or StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)


def _normalize_bar_frame(raw_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw_df is None or raw_df.empty:
        return pd.DataFrame()
    df = raw_df
    if isinstance(df.index, pd.MultiIndex):
        symbols = df.index.get_level_values(0)
        if symbol not in symbols:
            return pd.DataFrame()
        df = df.loc[symbol]
    return df.rename(columns={col: str(col).lower() for col in df.columns}).sort_index()


async def fetch_daily_bars(
    symbols: list[str],
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    limit: int = DEFAULT_BAR_LIMIT,
    client: StockHistoricalDataClient | None = None,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[str, pd.DataFrame]:
    if not symbols:
        return {}

    data_client = _client(client)
    sem = asyncio.Semaphore(max_concurrent)
    end_date = date.today()
    start_date = end_date - timedelta(days=lookback_days)

    async def fetch_one(symbol: str) -> tuple[str, pd.DataFrame]:
        async with sem:
            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=datetime.combine(start_date, datetime.min.time()),
                end=datetime.combine(end_date, datetime.min.time()),
                limit=limit,
                adjustment=Adjustment.RAW,
            )
            raw = await asyncio.to_thread(data_client.get_stock_bars, request)
            return symbol, _normalize_bar_frame(raw.df, symbol)

    results = await asyncio.gather(*(fetch_one(symbol) for symbol in symbols), return_exceptions=True)
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    for result in results:
        if isinstance(result, Exception):
            continue
        symbol, df = result
        if not df.empty:
            bars_by_symbol[symbol] = df
    return bars_by_symbol


def _value(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def snapshot_current_price(snapshot: Any) -> float | None:
    latest_trade = _value(snapshot, "latest_trade")
    minute_bar = _value(snapshot, "minute_bar")
    daily_bar = _value(snapshot, "daily_bar")
    for source in (latest_trade, minute_bar, daily_bar):
        value = _value(source, "price", "close")
        if value is not None:
            return float(value)
    return None


def snapshot_gap_pct(snapshot: Any) -> float | None:
    current_price = snapshot_current_price(snapshot)
    previous_bar = _value(snapshot, "previous_daily_bar", "prev_daily_bar")
    previous_close = _value(previous_bar, "close")
    if current_price is None or previous_close in (None, 0):
        return None
    return (current_price - float(previous_close)) / float(previous_close) * 100


async def fetch_premarket_gaps(
    symbols: list[str],
    *,
    client: StockHistoricalDataClient | None = None,
) -> dict[str, float]:
    if not symbols:
        return {}
    data_client = _client(client)
    request = StockSnapshotRequest(symbol_or_symbols=symbols)
    snapshots = await asyncio.to_thread(data_client.get_stock_snapshot, request)
    items = snapshots.items() if isinstance(snapshots, dict) else []
    gaps: dict[str, float] = {}
    for symbol, snapshot in items:
        gap = snapshot_gap_pct(snapshot)
        if gap is not None:
            gaps[symbol] = gap
    return gaps


async def fetch_latest_prices(
    symbols: list[str],
    *,
    client: StockHistoricalDataClient | None = None,
) -> dict[str, float]:
    if not symbols:
        return {}
    data_client = _client(client)
    request = StockSnapshotRequest(symbol_or_symbols=symbols)
    snapshots = await asyncio.to_thread(data_client.get_stock_snapshot, request)
    items = snapshots.items() if isinstance(snapshots, dict) else []
    prices: dict[str, float] = {}
    for symbol, snapshot in items:
        price = snapshot_current_price(snapshot)
        if price is not None:
            prices[symbol] = price
    return prices


async def fetch_latest_completed_hourly_bars(
    symbols: list[str],
    *,
    client: StockHistoricalDataClient | None = None,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[str, dict]:
    """Fetch the most recently completed hourly bar for each symbol.

    Hourly bars align to market open (9:30 ET), so completed bars start at
    9:30, 10:30, 11:30, … Returns empty dict if no bar has completed yet.
    """
    if not symbols:
        return {}

    now = datetime.now(tz=_ET)
    today = now.date()
    market_open = datetime.combine(today, time(9, 30), tzinfo=_ET)
    minutes_since_open = (now - market_open).total_seconds() / 60
    completed = int(minutes_since_open // 60)
    if completed < 1:
        return {}

    bar_start = market_open + timedelta(hours=completed - 1)
    bar_end = bar_start + timedelta(hours=1, minutes=1)

    data_client = _client(client)
    sem = asyncio.Semaphore(max_concurrent)

    async def fetch_one(symbol: str) -> tuple[str, dict | None]:
        async with sem:
            try:
                request = StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Hour,
                    start=bar_start,
                    end=bar_end,
                    limit=1,
                )
                raw = await asyncio.to_thread(data_client.get_stock_bars, request)
                df = _normalize_bar_frame(raw.df, symbol)
                if df.empty:
                    return symbol, None
                row = df.iloc[-1]
                return symbol, {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "vwap": float(row["vwap"]) if "vwap" in row and pd.notna(row["vwap"]) else None,
                }
            except Exception:
                return symbol, None

    results = await asyncio.gather(*(fetch_one(s) for s in symbols), return_exceptions=True)
    bars: dict[str, dict] = {}
    for result in results:
        if isinstance(result, Exception):
            continue
        symbol, bar = result
        if bar is not None:
            bars[symbol] = bar
    return bars


def _aggregate_intraday_bar(df: pd.DataFrame) -> dict | None:
    if df.empty:
        return None
    volume = float(df["volume"].sum())
    if volume <= 0:
        return None
    vwap = None
    if "vwap" in df:
        weighted_vwap = (df["vwap"].fillna(df["close"]) * df["volume"]).sum()
        vwap = float(weighted_vwap / volume)
    return {
        "open": float(df["open"].iloc[0]),
        "high": float(df["high"].max()),
        "low": float(df["low"].min()),
        "close": float(df["close"].iloc[-1]),
        "volume": volume,
        "vwap": vwap,
        "range_high": float(df["high"].iloc[:-1].max()) if len(df) > 1 else float(df["high"].iloc[-1]),
    }


async def fetch_latest_completed_65min_bars(
    symbols: list[str],
    *,
    client: StockHistoricalDataClient | None = None,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[str, dict]:
    """Fetch and aggregate the most recently completed 65-minute bar for each symbol."""
    if not symbols:
        return {}

    now = datetime.now(tz=_ET)
    today = now.date()
    market_open = datetime.combine(today, time(9, 30), tzinfo=_ET)
    minutes_since_open = (now - market_open).total_seconds() / 60
    completed = int(minutes_since_open // 65)
    if completed < 1:
        return {}

    bar_start = market_open + timedelta(minutes=65 * (completed - 1))
    bar_end = bar_start + timedelta(minutes=65)

    data_client = _client(client)
    sem = asyncio.Semaphore(max_concurrent)

    async def fetch_one(symbol: str) -> tuple[str, dict | None]:
        async with sem:
            try:
                request = StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Minute,
                    start=bar_start,
                    end=bar_end,
                    limit=65,
                )
                raw = await asyncio.to_thread(data_client.get_stock_bars, request)
                df = _normalize_bar_frame(raw.df, symbol)
                return symbol, _aggregate_intraday_bar(df)
            except Exception:
                return symbol, None

    results = await asyncio.gather(*(fetch_one(s) for s in symbols), return_exceptions=True)
    bars: dict[str, dict] = {}
    for result in results:
        if isinstance(result, Exception):
            continue
        symbol, bar = result
        if bar is not None:
            bars[symbol] = bar
    return bars


async def fetch_weekly_returns(symbols: list[str]) -> dict[str, float]:
    bars_by_symbol = await fetch_daily_bars(symbols, lookback_days=14, limit=10)
    returns: dict[str, float] = {}
    for symbol, df in bars_by_symbol.items():
        closes = [float(value) for value in df["close"].tail(6)]
        if len(closes) >= 2 and closes[0] != 0:
            returns[symbol] = (closes[-1] - closes[0]) / closes[0]
    return returns


def fetch_weekly_returns_sync(symbols: list[str]) -> dict[str, float]:
    return asyncio.run(fetch_weekly_returns(symbols))
