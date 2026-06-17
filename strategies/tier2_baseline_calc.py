"""
Tier-2 Baseline Calculator — scheduled for 3:45 AM ET.

Reads config/low_float_universe.json, fetches the last 20 daily bars
from Alpaca for each symbol, computes the 20-day Volume SMA and the
previous session close, then persists config/active_baselines.json.
"""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import Adjustment

from config.settings import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    SWING_UNIVERSE_PATH,
    VOLUME_SMA_PERIOD,
)
from config.rejection_tracker import rejection_tracker
from utils.indicators import atr, ema_last, fifty_two_week_high

_UNIVERSE_PATH  = Path(SWING_UNIVERSE_PATH)
_BASELINES_PATH = Path(__file__).parent.parent / "config" / "active_baselines.json"

# 420 calendar days spans roughly 252 trading days plus holiday slack.
_LOOKBACK_CAL_DAYS = 420
# Limit concurrent Alpaca bar requests to stay within free-tier rate limits.
_MAX_CONCURRENT = 10


# ── bar fetching ────────────────────────────────────────────────────────────

async def _fetch_bars(
    client: StockHistoricalDataClient,
    symbol: str,
    sem: asyncio.Semaphore,
    logger: logging.Logger,
) -> tuple[str, Optional[pd.DataFrame]]:
    """Return (symbol, DataFrame) or (symbol, None) on any error."""
    end_date   = date.today()
    start_date = end_date - timedelta(days=_LOOKBACK_CAL_DAYS)

    async with sem:
        try:
            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=datetime.combine(start_date, datetime.min.time()),
                end=datetime.combine(end_date, datetime.min.time()),
                limit=280,
                adjustment=Adjustment.RAW,
            )
            raw = await asyncio.to_thread(client.get_stock_bars, request)
            df: pd.DataFrame = raw.df
            if df is None or df.empty:
                logger.warning(f"Tier-2 | no bars returned for {symbol}")
                return symbol, None
            # alpaca-py returns a MultiIndex (symbol, timestamp) — drop symbol level.
            if isinstance(df.index, pd.MultiIndex):
                level_vals = df.index.get_level_values(0)
                if symbol not in level_vals:
                    logger.warning(f"Tier-2 | {symbol} missing from MultiIndex — skipping")
                    return symbol, None
                df = df.loc[symbol]
            return symbol, df.sort_index()
        except Exception as exc:
            logger.error(f"Tier-2 | bar fetch failed for {symbol}: {exc}")
            return symbol, None


# ── baseline computation ────────────────────────────────────────────────────

def _compute_baseline(df: pd.DataFrame) -> Optional[dict]:
    """
    Return baseline dict or None when data is insufficient.

    volume_sma_20  — mean of the last VOLUME_SMA_PERIOD daily volumes.
    previous_close — close price of the most recent completed session.
    vwap_last      — VWAP of the most recent bar (None if column absent).
    """
    if len(df) < VOLUME_SMA_PERIOD:
        return None

    window = df.tail(VOLUME_SMA_PERIOD)
    volume_sma    = float(np.mean(window["volume"].to_numpy()))
    previous_close = float(df["close"].iloc[-1])
    last_vwap = (
        float(df["vwap"].iloc[-1])
        if "vwap" in df.columns
        else None
    )
    atr_14 = atr(df, 14)
    ema20 = ema_last(df["close"], 20)
    ema50 = ema_last(df["close"], 50)
    high_52w = fifty_two_week_high(df)

    return {
        "volume_sma_20":  round(volume_sma, 2),
        "previous_close": round(previous_close, 4),
        "vwap_last":      round(last_vwap, 4) if last_vwap is not None else None,
        "atr_14":         round(atr_14, 4) if atr_14 is not None else None,
        "ema20":          round(ema20, 4) if ema20 is not None else None,
        "ema50":          round(ema50, 4) if ema50 is not None else None,
        "high_52w":       round(high_52w, 4) if high_52w is not None else None,
        "bars_used":      len(window),
    }


# ── public entry point ──────────────────────────────────────────────────────

async def run_baseline_calc() -> dict[str, dict]:
    """Fetch bars, compute baselines, persist JSON. Returns baselines dict."""
    logger = logging.getLogger("tier2_baseline_calc")

    if not _UNIVERSE_PATH.exists():
        logger.error(
            f"Tier-2 | universe file not found: {_UNIVERSE_PATH} — run Tier-1 first"
        )
        return {}

    text = _UNIVERSE_PATH.read_text().strip()
    tickers: list[str] = json.loads(text) if text else []
    if not tickers:
        logger.error("Tier-2 | universe file is empty — run Tier-1 first")
        return {}
    logger.info(f"Tier-2 | {len(tickers)} tickers loaded from universe")

    client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    fetch_results: list[tuple[str, Optional[pd.DataFrame]]] = await asyncio.gather(
        *[_fetch_bars(client, ticker, sem, logger) for ticker in tickers]
    )

    baselines: dict[str, dict] = {}
    skipped = 0

    for symbol, df in fetch_results:
        if df is None:
            rejection_tracker.record(symbol, "tier2", "no_bars_or_fetch_error")
            skipped += 1
            continue
        baseline = _compute_baseline(df)
        if baseline is None:
            rejection_tracker.record(symbol, "tier2", "insufficient_bars",
                                     bars_found=len(df), bars_needed=VOLUME_SMA_PERIOD)
            logger.warning(
                f"Tier-2 | {symbol}: only {len(df)} bars — need {VOLUME_SMA_PERIOD}, skipping"
            )
            skipped += 1
            continue
        baselines[symbol] = baseline

    logger.info(
        f"Tier-2 | complete — {len(baselines)} baselines computed, {skipped} skipped"
    )

    _BASELINES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _BASELINES_PATH.write_text(json.dumps(baselines, indent=2))
    logger.info(f"Tier-2 | saved → {_BASELINES_PATH}")

    return baselines
