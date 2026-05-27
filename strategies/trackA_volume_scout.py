"""
Track-A Volume Scout — 15-second async REST snapshot loop.

Every SNAPSHOT_INTERVAL_SECONDS during the regular session (9:30 AM – 4:00 PM ET):
  1. Read config/low_float_universe.json  →  symbol list
  2. Fetch a single batched GET /v2/stocks/snapshots from Alpaca
  3. Compute time-weighted RVOL_20 for each symbol against Tier-2 baselines
  4. Filter for RVOL_20 >= RVOL_MIN_THRESHOLD (2.0)
  5. Write the top HOT_WATCHLIST_SIZE performers to config.hot_watchlist
"""

import asyncio
import json
from pathlib import Path

import logging

import aiohttp

import config  # shared hot_watchlist + _watchlist_lock live here
from config.market_hours import elapsed_regular_minutes, is_regular_session, REGULAR_SESSION_MINUTES
from config.rejection_tracker import rejection_tracker
from config.settings import (
    ALPACA_API_KEY,
    ALPACA_DATA_FEED,
    ALPACA_DATA_URL,
    ALPACA_SECRET_KEY,
    HOT_WATCHLIST_SIZE,
    RVOL_MIN_THRESHOLD,
    SNAPSHOT_INTERVAL_SECONDS,
)

_UNIVERSE_PATH  = Path(__file__).parent.parent / "config" / "low_float_universe.json"
_BASELINES_PATH = Path(__file__).parent.parent / "config" / "active_baselines.json"
_SNAPSHOTS_URL  = f"{ALPACA_DATA_URL}/v2/stocks/snapshots"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


# ── RVOL calculation ─────────────────────────────────────────────────────────

def _calc_rvol_20(current_vol: int, sma_vol: float, elapsed_mins: float) -> float:
    """Time-weighted RVOL_20.

    Scales the 20-day average daily volume by the fraction of the session
    that has elapsed, then divides today's cumulative volume by that value.

        expected_vol = volume_sma_20  ×  (elapsed_mins / 390)
        RVOL_20      = current_vol    /  expected_vol

    A reading of 2.0 means the stock is pacing at twice its historical
    rate for this point in the session.
    """
    if elapsed_mins <= 0.0 or sma_vol <= 0.0:
        return 0.0
    expected = sma_vol * (elapsed_mins / REGULAR_SESSION_MINUTES)
    return current_vol / expected


# ── data I/O ─────────────────────────────────────────────────────────────────

def _load_universe() -> list[str]:
    text = _UNIVERSE_PATH.read_text().strip()
    return json.loads(text) if text else []


def _load_baselines() -> dict[str, dict]:
    text = _BASELINES_PATH.read_text().strip()
    return json.loads(text) if text else {}


async def _fetch_snapshots(
    session: aiohttp.ClientSession,
    symbols: list[str],
) -> dict:
    """Single batched GET to /v2/stocks/snapshots covering the full universe."""
    async with session.get(
        _SNAPSHOTS_URL,
        params={
            "symbols": ",".join(symbols),
            "feed":    ALPACA_DATA_FEED,
        },
        headers={
            "APCA-API-KEY-ID":     ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
        },
        timeout=_REQUEST_TIMEOUT,
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


# ── watchlist entry builder ──────────────────────────────────────────────────

def _build_entry(
    symbol:   str,
    snapshot: dict,
    baseline: dict,
    rvol:     float,
    elapsed:  float,
) -> dict:
    """Assemble a self-contained dict Track-B can consume without extra lookups."""
    daily_bar   = snapshot.get("dailyBar")    or {}
    latest_trade = snapshot.get("latestTrade") or {}
    latest_quote = snapshot.get("latestQuote") or {}
    return {
        "symbol":       symbol,
        "rvol_20":      round(rvol, 3),
        # Prefer live trade price; fall back to daily-bar close
        "price":        latest_trade.get("p") or daily_bar.get("c"),
        "bid":          latest_quote.get("bp"),
        "ask":          latest_quote.get("ap"),
        "volume":       daily_bar.get("v", 0),
        "vwap":         daily_bar.get("vw"),
        # Anchors from Tier-2 baselines — stable for the whole session
        "prev_close":   baseline.get("previous_close"),
        "volume_sma_20": baseline.get("volume_sma_20"),
        "elapsed_mins": round(elapsed, 1),
    }


# ── core cycle ───────────────────────────────────────────────────────────────

async def _scout_cycle(
    http:   aiohttp.ClientSession,
    logger: logging.Logger,
) -> None:
    """One iteration of the 15-second scout. No-op outside regular session."""
    if not is_regular_session():
        return

    elapsed = elapsed_regular_minutes()

    if not _UNIVERSE_PATH.exists() or not _BASELINES_PATH.exists():
        logger.warning(
            "TrackA | universe or baselines file missing — "
            "ensure Tier-1 and Tier-2 ran before market open"
        )
        return

    symbols   = _load_universe()
    baselines = _load_baselines()
    if not symbols:
        return

    try:
        snapshots = await _fetch_snapshots(http, symbols)
    except aiohttp.ClientResponseError as exc:
        logger.error(f"TrackA | HTTP {exc.status} from Alpaca snapshots: {exc.message}")
        return
    except asyncio.TimeoutError:
        logger.error("TrackA | snapshot request timed out (>10 s)")
        return
    except Exception as exc:
        logger.error(f"TrackA | snapshot fetch failed: {exc}")
        return

    symbols_set = set(symbols)
    candidates: list[dict] = []
    for symbol, snap in snapshots.items():
        baseline = baselines.get(symbol)
        if not baseline:
            if symbol in symbols_set:
                rejection_tracker.record(symbol, "track_a", "no_snapshot_data")
            continue
        daily_vol = (snap.get("dailyBar") or {}).get("v", 0)
        rvol = _calc_rvol_20(daily_vol, baseline["volume_sma_20"], elapsed)
        if rvol >= RVOL_MIN_THRESHOLD:
            candidates.append(_build_entry(symbol, snap, baseline, rvol, elapsed))
        else:
            rejection_tracker.record_rvol(symbol, rvol, RVOL_MIN_THRESHOLD)

    # Rank descending by RVOL; keep top-N only
    sorted_candidates = sorted(candidates, key=lambda e: e["rvol_20"], reverse=True)
    top_n = sorted_candidates[:HOT_WATCHLIST_SIZE]
    for entry in sorted_candidates[HOT_WATCHLIST_SIZE:]:
        rejection_tracker.record(entry["symbol"], "track_a", "above_threshold_not_top_n",
                                  rvol=entry["rvol_20"], top_n=HOT_WATCHLIST_SIZE)
    for entry in top_n:
        rejection_tracker.record_watchlist_entry(entry["symbol"])

    async with config._watchlist_lock:
        config.hot_watchlist = top_n

    if top_n:
        leaders = ", ".join(
            f"{e['symbol']} {e['rvol_20']:.1f}x @ ${e['price']}"
            for e in top_n[:3]
        )
        logger.info(
            f"TrackA | {len(candidates)} ≥ {RVOL_MIN_THRESHOLD}x "
            f"→ watchlist={len(top_n)} | {leaders} | {elapsed:.0f} min elapsed"
        )
    else:
        logger.debug(
            f"TrackA | no symbols above {RVOL_MIN_THRESHOLD}x RVOL at {elapsed:.0f} min"
        )


# ── public entry point ───────────────────────────────────────────────────────

async def run_volume_scout() -> None:
    """
    Persistent async loop called by main.py as an asyncio Task.

    Runs _scout_cycle every SNAPSHOT_INTERVAL_SECONDS until the task is
    cancelled (e.g., at market close). The single aiohttp.ClientSession
    is reused across all cycles to avoid TCP handshake overhead.
    """
    logger = logging.getLogger("trackA_volume_scout")
    logger.info(
        f"TrackA | volume scout started "
        f"(interval={SNAPSHOT_INTERVAL_SECONDS:.0f}s, "
        f"rvol_min={RVOL_MIN_THRESHOLD}x, "
        f"top_n={HOT_WATCHLIST_SIZE})"
    )

    async with aiohttp.ClientSession() as http:
        while True:
            try:
                await _scout_cycle(http, logger)
            except asyncio.CancelledError:
                logger.info("TrackA | task cancelled — shutting down")
                raise
            except Exception as exc:
                # Log and continue — a single bad cycle must not kill the loop
                logger.error(f"TrackA | unhandled exception in cycle: {exc}")

            await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)
