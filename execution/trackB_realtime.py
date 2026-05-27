"""
Track-B Real-Time Engine — Alpaca WebSocket tick-by-tick handler.

Entry trigger (fires at most once per symbol per session)
──────────────────────────────────────────────────────────
  All sessions: cvd > 0  AND  tick_dir == +1
                AND all indicator filters pass (VWAP, EMA9/20, MACD, L2)

Dynamic subscriptions
─────────────────────
  A background task polls config.hot_watchlist every SNAPSHOT_INTERVAL_SECONDS.
  Symbols added/dropped get incremental subscribe/unsubscribe messages —
  the main WebSocket connection is never torn down on a watchlist change.
"""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import logging

import websockets

import config
from config.market_hours import is_regular_session
from config.rejection_tracker import rejection_tracker
from config.settings import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_WS_URL,
    GAP_PCT_MIN_THRESHOLD,
    SNAPSHOT_INTERVAL_SECONDS,
)
from agents.news_analyst import evaluate_news
from execution.position_manager import signal_long_entry
import execution.position_manager as _pm

_log = logging.getLogger("trackB_realtime")
_BASELINES_PATH = Path(__file__).parent.parent / "config" / "active_baselines.json"
_UNIVERSE_PATH  = Path(__file__).parent.parent / "config" / "low_float_universe.json"


def _load_universe() -> list[str]:
    if not _UNIVERSE_PATH.exists():
        return []
    text = _UNIVERSE_PATH.read_text().strip()
    return json.loads(text) if text else []


# ── per-symbol state ──────────────────────────────────────────────────────────

@dataclass
class SymbolState:
    cvd:           float = 0.0
    last_price:    float = 0.0
    last_tick_dir: int   = 0
    chg_pct:       float = 0.0
    volume:        int   = 0

    # VWAP (session accumulator)
    vwap:      float = 0.0
    vwap_prev: float = 0.0   # VWAP before current tick — used by entry filter
    vwap_pv:   float = 0.0
    vwap_v:    float = 0.0

    # EMAs (source = trade price)
    ema9:        float = 0.0
    ema9_n:      int   = 0
    ema20:       float = 0.0
    ema20_n:     int   = 0
    ema12:       float = 0.0
    ema12_n:     int   = 0
    ema26:       float = 0.0
    ema26_n:     int   = 0

    # MACD (fast=12, slow=26, signal smoothing=9)
    macd_line:      float = 0.0
    macd_signal:    float = 0.0
    macd_hist:      float = 0.0
    macd_hist_prev: float = 0.0   # histogram value on the previous tick
    macd_n:         int   = 0     # ticks since MACD line became valid

    # Level 2 order book (top 5 bid/ask levels: [(price, size), ...])
    bids: list = field(default_factory=list)
    asks: list = field(default_factory=list)


# ── CVD: Lee-Ready tick-rule ──────────────────────────────────────────────────

def _tick_direction(price: float, prev_price: float, prev_dir: int) -> int:
    """
    Classify a trade as buy (+1) or sell (-1) aggressor.

    Uses the Lee-Ready tick rule:
      price > prev  → uptick  → buyer aggressor  (+1)
      price < prev  → downtick → seller aggressor (-1)
      price == prev → inherit previous direction  (zero-tick)
      prev == 0.0   → indeterminate first tick    ( 0)
    """
    if prev_price == 0.0:
        return 0
    if price > prev_price:
        return +1
    if price < prev_price:
        return -1
    return prev_dir


# ── EMA helper ───────────────────────────────────────────────────────────────

_EMA_K = {n: 2.0 / (n + 1) for n in (9, 12, 20, 26)}


def _ema_step(prev: float, n_seen: int, price: float, period: int) -> tuple[float, int]:
    n_seen += 1
    if n_seen == 1:
        return price, n_seen
    k = _EMA_K[period]
    return prev + k * (price - prev), n_seen


# ── indicator filter ─────────────────────────────────────────────────────────

def _indicator_reject_reason(state: SymbolState, price: float) -> str:
    """
    Returns an empty string when all indicator conditions pass.
    Returns a human-readable reason string when any condition fails.

    Filters applied (each skipped gracefully when not yet warmed up):
      VWAP  — price must be above session VWAP
      EMA   — price > EMA9 AND EMA9 > EMA20  (bullish alignment)
      MACD  — histogram > 0  (bullish momentum; requires ≥9 MACD values)
      L2    — top-3 bid depth ≥ top-3 ask depth  (buying pressure dominant)
    """
    if state.vwap_prev > 0 and price <= state.vwap_prev:
        return f"price≤vwap ({price:.4f}≤{state.vwap_prev:.4f})"

    if state.ema9_n >= 2 and state.ema20_n >= 2:
        if price <= state.ema9:
            return f"price≤ema9 ({price:.4f}≤{state.ema9:.4f})"
        if state.ema9 <= state.ema20:
            return f"ema9≤ema20 ({state.ema9:.4f}≤{state.ema20:.4f})"

    if state.macd_n >= 9:
        if state.macd_hist <= 0:
            return f"macd_hist≤0 ({state.macd_hist:.5f})"
        if state.macd_hist_prev > 0:
            return f"macd no crossover (prev={state.macd_hist_prev:.5f})"

    if state.bids and state.asks:
        bid_depth = sum(s for _, s in state.bids[:3])
        ask_depth = sum(s for _, s in state.asks[:3])
        if bid_depth < ask_depth:
            return f"l2 bid<ask ({bid_depth}<{ask_depth})"

    return ""


# ── core trade processor (pure — no I/O, no awaits) ──────────────────────────

def _process_trade(
    msg:       dict,
    states:    dict[str, SymbolState],
    baselines: dict[str, dict],
) -> Optional[dict]:
    """
    Update per-symbol state for one trade tick.

    Identical logic pre-market, regular, and after-market:
      ORB windows    : 4:00–4:05 AM and 9:30–9:35 AM — track High/Low.
      Outside ORB    : CVD, Chg%, and ORB-breakout entry check.

    Alpaca trade message fields used
    ─────────────────────────────────
      S  str    symbol
      p  float  trade price
      s  int    trade size (shares)
    """
    symbol = msg.get("S")
    price  = msg.get("p")
    size   = msg.get("s", 0)
    if not symbol or price is None:
        return None

    state = states.setdefault(symbol, SymbolState())

    baseline   = baselines.get(symbol, {})
    prev_close = baseline.get("previous_close", 0.0)

    if prev_close:
        state.chg_pct = round((price - prev_close) / prev_close * 100, 3)

    tick_dir            = _tick_direction(price, state.last_price, state.last_tick_dir)
    state.cvd          += size * tick_dir
    state.last_tick_dir = tick_dir
    state.last_price    = price
    state.volume       += size

    # ── indicators (computed every tick, all sessions) ────────────────────────
    if size > 0:
        state.vwap_prev = state.vwap          # snapshot before this tick
        state.vwap_pv  += price * size
        state.vwap_v   += size
        state.vwap      = state.vwap_pv / state.vwap_v

    state.ema9,  state.ema9_n  = _ema_step(state.ema9,  state.ema9_n,  price, 9)
    state.ema20, state.ema20_n = _ema_step(state.ema20, state.ema20_n, price, 20)
    state.ema12, state.ema12_n = _ema_step(state.ema12, state.ema12_n, price, 12)
    state.ema26, state.ema26_n = _ema_step(state.ema26, state.ema26_n, price, 26)

    if state.ema12_n > 1 and state.ema26_n > 1:
        state.macd_hist_prev = state.macd_hist
        macd                 = state.ema12 - state.ema26
        state.macd_line      = macd
        state.macd_signal, state.macd_n = _ema_step(
            state.macd_signal, state.macd_n, macd, 9
        )
        state.macd_hist = state.macd_line - state.macd_signal

    if (
        state.cvd > 0
        and tick_dir == +1
        and not _indicator_reject_reason(state, price)
    ):
        return {
            "symbol":  symbol,
            "price":   price,
            "cvd":     round(state.cvd, 2),
            "chg_pct":     state.chg_pct,
            "volume":      state.volume,
            "ema9":        round(state.ema9, 4),
            "ema20":       round(state.ema20, 4),
            "vwap":        round(state.vwap, 4),
            "macd":        round(state.macd_line, 5),
            "macd_signal": round(state.macd_signal, 5),
            "macd_hist":   round(state.macd_hist, 5),
        }

    return None


# ── WebSocket helpers ─────────────────────────────────────────────────────────

async def _ws_send(ws, payload: dict) -> None:
    await ws.send(json.dumps(payload))


async def _authenticate(ws, logger: logging.Logger) -> None:
    await _ws_send(ws, {
        "action": "auth",
        "key":    ALPACA_API_KEY,
        "secret": ALPACA_SECRET_KEY,
    })
    raw  = await ws.recv()
    msgs = json.loads(raw)
    if not any(m.get("T") == "success" for m in msgs):
        raise ConnectionError(f"Alpaca WS auth failed: {msgs}")
    logger.info("TrackB | authenticated")


# ── subscription manager (background task) ────────────────────────────────────

async def _subscription_manager(
    ws:     Any,
    states: dict[str, SymbolState],
    logger: logging.Logger,
) -> None:
    """
    Polls session state every SNAPSHOT_INTERVAL_SECONDS and sends incremental
    subscribe/unsubscribe messages — never tears down the connection.

    Only active during regular market hours (9:30 AM – 4:00 PM ET).
    Outside those hours all subscriptions are cleared.
    """
    trade_subs: set[str] = set()
    news_subs:  set[str] = set()

    while True:
        await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)

        if not is_regular_session():
            if trade_subs:
                await _ws_send(ws, {
                    "action":     "unsubscribe",
                    "trades":     sorted(trade_subs),
                    "orderbooks": sorted(trade_subs),
                })
                for sym in trade_subs:
                    states.pop(sym, None)
                logger.info("TrackB | outside market hours — unsubscribed all trades")
                trade_subs = set()
            if news_subs:
                await _ws_send(ws, {"action": "unsubscribe", "news": sorted(news_subs)})
                logger.info("TrackB | outside market hours — unsubscribed all news")
                news_subs = set()
            continue

        async with config._watchlist_lock:
            wl = {e["symbol"] for e in config.hot_watchlist}
        trade_want = wl
        news_want  = wl

        to_add_trades = trade_want - trade_subs
        to_rm_trades  = trade_subs  - trade_want
        if to_add_trades:
            await _ws_send(ws, {
                "action":     "subscribe",
                "trades":     sorted(to_add_trades),
                "orderbooks": sorted(to_add_trades),
            })
            logger.info(f"TrackB | subscribed trades   +{sorted(to_add_trades)}")
        if to_rm_trades:
            await _ws_send(ws, {
                "action":     "unsubscribe",
                "trades":     sorted(to_rm_trades),
                "orderbooks": sorted(to_rm_trades),
            })
            for sym in to_rm_trades:
                states.pop(sym, None)
            logger.info(f"TrackB | unsubscribed trades -{sorted(to_rm_trades)}")
        trade_subs = trade_want

        to_add_news = news_want - news_subs
        to_rm_news  = news_subs  - news_want
        if to_add_news:
            await _ws_send(ws, {"action": "subscribe",   "news": sorted(to_add_news)})
            logger.info(f"TrackB | subscribed news     +{sorted(to_add_news)}")
        if to_rm_news:
            await _ws_send(ws, {"action": "unsubscribe", "news": sorted(to_rm_news)})
            logger.info(f"TrackB | unsubscribed news   -{sorted(to_rm_news)}")
        news_subs = news_want


# ── news handler ─────────────────────────────────────────────────────────────

async def _handle_news_tick(
    msg:    dict,
    states: dict[str, SymbolState],
) -> None:
    """Evaluate a live news frame — blocks bad tickers; no entry fired directly."""
    await evaluate_news(msg)


# ── receive loop (strictly non-blocking) ──────────────────────────────────────

async def _receive_loop(
    ws:        Any,
    states:    dict[str, SymbolState],
    baselines: dict[str, dict],
    logger:    logging.Logger,
) -> None:
    """
    Process every incoming WebSocket frame.

    Trade processing (_process_trade) is a pure synchronous call — no I/O,
    no sleeps, just arithmetic — so it cannot block the event loop.
    News evaluation and entry signals are dispatched with create_task so
    their latency never stalls the receive loop.
    """
    async for raw in ws:
        try:
            messages: list[dict] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        for msg in messages:
            msg_type = msg.get("T")

            if msg_type == "t":                          # trade tick
                sym   = msg.get("S")
                price = msg.get("p")
                size  = msg.get("s", 0)

                signal = _process_trade(msg, states, baselines)
                if signal:
                    _sym = signal["symbol"]
                    if signal["chg_pct"] < GAP_PCT_MIN_THRESHOLD:
                        logger.debug("TrackB | %s skipped — gap %.2f%% below threshold", _sym, signal["chg_pct"])
                        rejection_tracker.record(
                            _sym, "track_b", "gap_below_threshold",
                            gap_pct=round(signal["chg_pct"], 2),
                            threshold=GAP_PCT_MIN_THRESHOLD,
                        )
                    elif _sym not in _pm._positions:
                        _sym_state = states.get(_sym)
                        ask_price  = _sym_state.asks[0][0] if (_sym_state and _sym_state.asks) else 0.0
                        asyncio.create_task(
                            signal_long_entry(
                                symbol=_sym,
                                price=signal["price"],
                                ask=ask_price,
                                cvd=signal["cvd"],
                                chg_pct=signal["chg_pct"],
                                volume=signal["volume"],
                            ),
                            name=f"entry-{_sym}",
                        )
                        logger.info(
                            "TrackB | ENTRY  %s  price=%.4f  ask=%.4f  cvd=%.0f  "
                            "ema9=%.4f  ema20=%.4f  vwap=%.4f  "
                            "macd=%.5f  sig=%.5f  hist=%.5f  chg=%.2f%%",
                            _sym, signal["price"], ask_price, signal["cvd"],
                            signal["ema9"], signal["ema20"], signal["vwap"],
                            signal["macd"], signal["macd_signal"], signal["macd_hist"],
                            signal["chg_pct"],
                        )

                else:
                    # No signal — record WHY (first occurrence per symbol)
                    if sym and price is not None:
                        _st = states.get(sym)
                        if _st and _st.last_price > 0:
                            if _st.cvd > 0 and _st.last_tick_dir == 1:
                                _rej = _indicator_reject_reason(_st, price)
                                if _rej:
                                    rejection_tracker.record(
                                        sym, "track_b_indicator",
                                        _rej.split("(")[0].strip(),
                                        detail=_rej,
                                    )
                            elif _st.cvd <= 0:
                                rejection_tracker.record(
                                    sym, "track_b", "cvd_nonpositive",
                                    cvd=round(_st.cvd),
                                )

                # Feed every tick into position manager for exit logic
                if sym and price is not None and sym in _pm._positions:
                    _st = states.get(sym)
                    asyncio.create_task(
                        _pm.on_price_tick(
                            sym, price, size,
                            tick_dir=_st.last_tick_dir if _st else 0,
                            bids=_st.bids if _st else [],
                            asks=_st.asks if _st else [],
                        ),
                        name=f"pm-tick-{sym}",
                    )

            elif msg_type == "n":
                asyncio.create_task(
                    _handle_news_tick(msg, states),
                    name=f"news-{msg.get('id', 'x')}",
                )

            elif msg_type == "o":                        # Level 2 orderbook
                sym = msg.get("S")
                if sym and sym in states:
                    states[sym].bids = [
                        (b["p"], b["s"]) for b in (msg.get("bids") or [])[:5]
                    ]
                    states[sym].asks = [
                        (a["p"], a["s"]) for a in (msg.get("asks") or [])[:5]
                    ]

            elif msg_type in ("error",):
                logger.error(f"TrackB | WS error frame: {msg}")

            # "subscription" and "success" control frames → silently ignore


# ── public entry point ────────────────────────────────────────────────────────

async def run_realtime_engine() -> None:
    """
    Persistent WebSocket engine called by main.py as an asyncio Task.

    Reconnects with exponential backoff (1 s → 2 → 4 → … → 60 s cap) on
    any connection failure.  Per-symbol SymbolState is preserved across
    reconnects within the same session.  Baselines are re-read from disk
    on each connect attempt so a Tier-2 re-run is picked up automatically.
    """
    logger = logging.getLogger("trackB_realtime")
    logger.info(f"TrackB | engine starting  url={ALPACA_WS_URL}")

    states:  dict[str, SymbolState] = {}
    backoff: int = 1

    while True:
        # Re-read baselines on each connect so a Tier-2 re-run is picked up.
        baselines: dict[str, dict] = {}
        if _BASELINES_PATH.exists():
            try:
                baselines = json.loads(_BASELINES_PATH.read_text())
            except Exception as exc:
                logger.warning(f"TrackB | could not load baselines: {exc}")

        try:
            async with websockets.connect(
                ALPACA_WS_URL,
                ping_interval=20,
                ping_timeout=10,
                max_size=2 ** 23,      # 8 MB — handles large news payloads
            ) as ws:
                backoff = 1            # reset on clean connect

                await ws.recv()        # consume server "connected" frame
                await _authenticate(ws, logger)

                # Subscribe to current hot_watchlist on connect (regular hours only)
                if is_regular_session():
                    async with config._watchlist_lock:
                        initial = [e["symbol"] for e in config.hot_watchlist]
                    if initial:
                        await _ws_send(ws, {
                            "action":     "subscribe",
                            "trades":     initial,
                            "news":       initial,
                            "orderbooks": initial,
                        })
                        logger.info(f"TrackB | initial subscribe: {initial}")

                sub_task = asyncio.create_task(
                    _subscription_manager(ws, states, logger),
                    name="trackB-sub-manager",
                )
                try:
                    await _receive_loop(ws, states, baselines, logger)
                finally:
                    sub_task.cancel()

        except asyncio.CancelledError:
            logger.info("TrackB | cancelled — shutting down")
            raise

        except Exception as exc:
            logger.error(
                f"TrackB | connection lost ({type(exc).__name__}: {exc}) "
                f"— reconnect in {backoff}s"
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
