"""
Position Manager — split-bracket exit engine with dynamic limit orders.

Public API: signal_long_entry, on_price_tick, block_ticker, run_trade_updates.

T1  (50 %) : static GTC limit at entry + 4 %; placed on entry fill.
Runner (50%): break-even shift on T1 fill; trail 3 %; exit at VWAP+3σ;
              emergency liquidation on −$40 float or 1-min close < VWAP.
All exits: limit_price = bid − min(0.75 % × price, $0.10).
Watchdog  : runner/emergency orders still open after 2 s are cancelled and
            replaced with a 1.5× widened limit.
"""

import asyncio
import json
import math
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as dt_time
from enum import Enum, auto
from typing import Optional
from zoneinfo import ZoneInfo

import aiohttp
import websockets
from aiologger import Logger
from aiologger.formatters.base import Formatter
from aiologger.handlers.streams import AsyncStreamHandler
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

import config
from agents.telegram_notifier import send_entry_alert, send_exit_alert
from config.rejection_tracker import rejection_tracker
from config.trade_logger import trade_logger
from config.settings import (
    ALPACA_API_KEY,
    ALPACA_DATA_URL,
    ALPACA_SECRET_KEY,
    MAX_POSITION_COST,
    MAX_RISK_PER_TRADE,
)

_fmt = Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")
_log = Logger(name=__name__)
_log.add_handler(AsyncStreamHandler(stream=sys.stdout, formatter=_fmt))

_TRADE_UPDATES_URL = "wss://paper-api.alpaca.markets/stream"
_QUOTES_URL        = f"{ALPACA_DATA_URL}/v2/stocks/quotes/latest"
_T1_GAIN_PCT       = 0.04
_TRAIL_PCT         = 0.03
_TIGHT_TRAIL_PCT   = 0.015   # tightened when L2/T&S shows mild sell pressure
_VWAP_STD_BANDS    = 3.0
_SELL_OFFSET       = 0.05    # fixed offset below bid for all exit limit orders
_BUY_OFFSET        = 0.05    # fixed offset above ask for entry limit orders
_WATCHDOG_SECS     = 2.0
_WATCHDOG_MULT     = 1.5
_ENTRY_TIMEOUT     = 10.0
_L2_MILD_SELL_SIZE    = 500   # shares on a single downtick trade to flag pressure
_L2_SEVERE_ASK_RATIO  = 3.0  # ask_depth / bid_depth to trigger emergency exit
_EOD_TIME          = dt_time(19, 55)
_ET                = ZoneInfo("America/New_York")
_MIN_SHARES        = 1


# ── status ────────────────────────────────────────────────────────────────────

class _Status(Enum):
    PENDING_ENTRY = auto()
    OPEN          = auto()
    CLOSING       = auto()
    CLOSED        = auto()


# ── per-symbol state ──────────────────────────────────────────────────────────

@dataclass
class _PositionState:
    symbol:         str
    entry_order_id: str

    entry_price:   float = 0.0
    total_shares:  int   = 0
    t1_shares:     int   = 0
    runner_shares: int   = 0

    t1_order_id:          Optional[str] = None
    runner_exit_order_id: Optional[str] = None

    highest_price_seen: float = 0.0
    stop_price:         float = 0.0
    trail_pct:          float = _TRAIL_PCT
    break_even_active:  bool  = False
    t1_filled:          bool  = False
    t1_fill_price:      float = 0.0
    runner_exited:      bool  = False
    rvol_20:            float = 0.0

    _pv:  float = 0.0   # Σ(p·v)
    _v:   float = 0.0   # Σ(v)
    _p2v: float = 0.0   # Σ(p²·v)

    candle_minute: int   = -1
    candle_open:   float = 0.0
    candle_close:  float = 0.0

    exit_order_placed_at: float                  = 0.0
    exit_reason:          str                    = ""
    watchdog_task:        Optional[asyncio.Task] = field(default=None, compare=False)
    entry_timeout_task:   Optional[asyncio.Task] = field(default=None, compare=False)

    status: _Status = _Status.PENDING_ENTRY


# ── module state ──────────────────────────────────────────────────────────────

_positions:      dict[str, _PositionState] = {}
_trading_client: Optional[TradingClient]   = None


def _client() -> TradingClient:
    global _trading_client
    if _trading_client is None:
        _trading_client = TradingClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
            paper=True,
        )
    return _trading_client


# ── math helpers ──────────────────────────────────────────────────────────────

def _lx(bid: float, mult: float = 1.0) -> float:
    return round(bid - _SELL_OFFSET * mult, 2)


def _vwap_sigma(s: _PositionState) -> tuple[float, float]:
    if s._v <= 0:
        return 0.0, 0.0
    vwap = s._pv / s._v
    var  = s._p2v / s._v - vwap ** 2
    return vwap, math.sqrt(max(var, 0.0))


def _held_qty(s: _PositionState) -> int:
    return s.runner_shares + (0 if s.t1_filled else s.t1_shares)


# ── order helpers ─────────────────────────────────────────────────────────────

async def _sell_limit(symbol: str, qty: int, price: float, tag: str) -> Optional[str]:
    try:
        req = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=round(price, 2),
            extended_hours=True,
        )
        order = await asyncio.to_thread(_client().submit_order, req)
        await _log.info("PM | %s SELL-LIMIT  tag=%s  qty=%d  lx=%.4f  id=%s",
                        symbol, tag, qty, price, order.id)
        return str(order.id)
    except Exception as exc:
        await _log.error("PM | %s sell-limit failed  tag=%s: %s", symbol, tag, exc)
        return None


async def _cancel_order(order_id: str, symbol: str, tag: str) -> None:
    try:
        await asyncio.to_thread(_client().cancel_order_by_id, order_id)
        await _log.info("PM | %s cancelled  tag=%s  id=%s", symbol, tag, order_id)
    except Exception as exc:
        await _log.warning("PM | %s cancel failed  tag=%s  id=%s: %s", symbol, tag, order_id, exc)


async def _fetch_order_status(order_id: str) -> Optional[str]:
    try:
        o = await asyncio.to_thread(_client().get_order_by_id, order_id)
        return str(o.status)
    except Exception:
        return None


def _cached_bid(symbol: str) -> float:
    for e in config.hot_watchlist:
        if e.get("symbol") == symbol:
            return float(e.get("bid") or 0)
    return 0.0


def _cached_rvol(symbol: str) -> float:
    for e in config.hot_watchlist:
        if e.get("symbol") == symbol:
            return float(e.get("rvol_20") or 0)
    return 0.0


def _calc_exit_pnl(s: "_PositionState", exit_price: float) -> float:
    if s.t1_filled:
        t1_pnl = (s.t1_fill_price - s.entry_price) * s.t1_shares
        return t1_pnl + (exit_price - s.entry_price) * s.runner_shares
    return (exit_price - s.entry_price) * _held_qty(s)


async def block_ticker(symbol: str) -> None:
    async with config._blocked_lock:
        config.blocked_tickers.add(symbol)
    await _log.warning("PM | %s BLOCKED by news analyst", symbol)


# ── public: entry ─────────────────────────────────────────────────────────────

async def signal_long_entry(
    *,
    symbol:  str,
    price:   float,
    ask:     float = 0.0,
    cvd:     float,
    chg_pct: float,
    volume:  int,
) -> None:
    async with config._blocked_lock:
        if symbol in config.blocked_tickers:
            await _log.info("PM | %s suppressed — blocked by news analyst", symbol)
            rejection_tracker.record(symbol, "pm_news_blocked", "entry_suppressed_by_news_block")
            return

    if symbol in _positions or price <= 0:
        return

    shares = max(_MIN_SHARES, int(MAX_POSITION_COST / price))

    # Reserve slot before any await so concurrent entry signals are blocked immediately.
    s = _PositionState(symbol=symbol, entry_order_id="pending")
    _positions[symbol] = s

    limit_px = round((ask if ask > 0 else price) + _BUY_OFFSET, 2)
    try:
        req = LimitOrderRequest(
            symbol=symbol,
            qty=shares,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=limit_px,
            extended_hours=True,
        )
        order = await asyncio.to_thread(_client().submit_order, req)
    except Exception as exc:
        _positions.pop(symbol, None)
        await _log.error("PM | %s entry order failed: %s", symbol, exc)
        return

    s.entry_order_id = str(order.id)
    s.rvol_20 = _cached_rvol(symbol)
    trade_logger.on_entry_submitted(symbol, shares, limit_px, cvd, chg_pct, volume)
    s.entry_timeout_task = asyncio.create_task(
        _entry_timeout(s), name=f"entry-timeout-{symbol}"
    )
    await _log.info(
        "PM | ENTRY SUBMITTED  %s  qty=%d  lx=%.4f  cvd=%.0f  chg=%.2f%%  id=%s",
        symbol, shares, limit_px, cvd, chg_pct, order.id,
    )


# ── fill handlers ─────────────────────────────────────────────────────────────

async def _on_entry_fill(s: _PositionState, fill_px: float, fill_qty: int) -> None:
    if s.entry_timeout_task and not s.entry_timeout_task.done():
        s.entry_timeout_task.cancel()
    s.entry_price        = fill_px
    s.total_shares       = fill_qty
    s.t1_shares          = fill_qty // 2
    s.runner_shares      = fill_qty - s.t1_shares
    s.highest_price_seen = fill_px
    s.stop_price         = fill_px - MAX_RISK_PER_TRADE / max(fill_qty, 1)
    s.status             = _Status.OPEN

    rejection_tracker.record_traded(s.symbol)
    trade_logger.on_entry_filled(
        s.symbol, fill_px, fill_qty, s.t1_shares, s.runner_shares, s.stop_price, s.rvol_20
    )
    await _log.info(
        "PM | ENTRY FILLED  %s  fill=%.4f  qty=%d  t1=%d  runner=%d  stop=%.4f",
        s.symbol, fill_px, fill_qty, s.t1_shares, s.runner_shares, s.stop_price,
    )
    asyncio.create_task(
        send_entry_alert(s.symbol, fill_px, fill_qty, fill_px * fill_qty),
        name=f"tg-entry-{s.symbol}",
    )

    if s.t1_shares < 1:
        return

    t1_px = round(fill_px * (1 + _T1_GAIN_PCT), 2)
    oid = await _sell_limit(s.symbol, s.t1_shares, t1_px, "T1")
    if oid:
        s.t1_order_id = oid


async def _on_t1_fill(s: _PositionState, fill_px: float) -> None:
    s.t1_filled         = True
    s.t1_fill_price     = fill_px
    s.break_even_active = True
    s.stop_price        = s.entry_price
    trade_logger.on_t1_filled(s.symbol, fill_px)
    await _log.info(
        "PM | T1 FILLED  %s  t1_px=%.4f  stop → break-even=%.4f  runner=%d",
        s.symbol, fill_px, s.entry_price, s.runner_shares,
    )


async def _on_runner_fill(s: _PositionState, fill_px: float) -> None:
    if s.t1_order_id and not s.t1_filled:
        await _cancel_order(s.t1_order_id, s.symbol, "T1-cancel-on-runner-exit")
    pnl = _calc_exit_pnl(s, fill_px)
    s.runner_exited = True
    s.status        = _Status.CLOSED
    _cancel_watchdog(s)
    _positions.pop(s.symbol, None)
    trade_logger.on_closed(s.symbol, fill_px, s.exit_reason or "RUNNER_FILL", pnl)
    await _log.info("PM | POSITION CLOSED  %s  pnl=%.2f", s.symbol, pnl)
    asyncio.create_task(
        send_exit_alert(
            symbol=s.symbol,
            exit_price=fill_px,
            reason="RUNNER_FILL",
            entry_price=s.entry_price,
            pnl=pnl,
            rvol=s.rvol_20,
            t1_filled=s.t1_filled,
            t1_fill_price=s.t1_fill_price,
            t1_shares=s.t1_shares,
            runner_shares=s.runner_shares,
        ),
        name=f"tg-exit-{s.symbol}",
    )


# ── exit helpers ──────────────────────────────────────────────────────────────

async def _exit_runner(s: _PositionState, bid: float, price: float, reason: str) -> None:
    if s.runner_exited or s.status == _Status.CLOSED or s.runner_shares < 1:
        return
    s.status = _Status.CLOSING
    s.exit_reason = reason
    lx = _lx(bid)
    oid = await _sell_limit(s.symbol, s.runner_shares, lx, reason)
    if oid:
        s.runner_exit_order_id = oid
        s.exit_order_placed_at = time.monotonic()
        _start_watchdog(s, oid, s.runner_shares)
    await _log.warning("PM | RUNNER EXIT  %s  reason=%s  qty=%d  lx=%.4f",
                       s.symbol, reason, s.runner_shares, lx)


async def _emergency_liquidate(s: _PositionState, bid: float, price: float, reason: str) -> None:
    _cancel_watchdog(s)
    s.status = _Status.CLOSING
    s.exit_reason = reason

    if s.t1_order_id and not s.t1_filled:
        await _cancel_order(s.t1_order_id, s.symbol, "T1-emergency")
    if s.runner_exit_order_id and not s.runner_exited:
        await _cancel_order(s.runner_exit_order_id, s.symbol, "runner-emergency")

    held = _held_qty(s)
    if held < 1:
        _positions.pop(s.symbol, None)
        return

    lx = _lx(bid)
    oid = await _sell_limit(s.symbol, held, lx, f"EMERGENCY:{reason}")
    if oid:
        s.runner_exit_order_id = oid
        s.exit_order_placed_at = time.monotonic()
        _start_watchdog(s, oid, held)
    await _log.error("PM | EMERGENCY EXIT  %s  reason=%s  qty=%d  lx=%.4f",
                     s.symbol, reason, held, lx)


# ── entry timeout ────────────────────────────────────────────────────────────

async def _entry_timeout(s: _PositionState) -> None:
    await asyncio.sleep(_ENTRY_TIMEOUT)
    if s.status != _Status.PENDING_ENTRY:
        return
    await _cancel_order(s.entry_order_id, s.symbol, "entry-timeout")
    _positions.pop(s.symbol, None)
    trade_logger.on_entry_timeout(s.symbol)
    await _log.warning("PM | %s entry order cancelled — %.0fs timeout", s.symbol, _ENTRY_TIMEOUT)


# ── partial-fill watchdog ─────────────────────────────────────────────────────

def _start_watchdog(s: _PositionState, order_id: str, qty: int) -> None:
    _cancel_watchdog(s)
    s.watchdog_task = asyncio.create_task(
        _watchdog(s, order_id, qty), name=f"watchdog-{s.symbol}"
    )


def _cancel_watchdog(s: _PositionState) -> None:
    if s.watchdog_task and not s.watchdog_task.done():
        s.watchdog_task.cancel()
    s.watchdog_task = None


async def _watchdog(s: _PositionState, order_id: str, qty: int) -> None:
    await asyncio.sleep(_WATCHDOG_SECS)

    status = await _fetch_order_status(order_id)
    if status in (None, "filled", "canceled", "expired", "done_for_day"):
        return

    await _log.warning("PM | watchdog fired  %s  order=%s  status=%s  qty=%d",
                       s.symbol, order_id, status, qty)

    await _cancel_order(order_id, s.symbol, "watchdog")

    bid = _cached_bid(s.symbol)
    if bid <= 0:
        await _log.error("PM | watchdog: no cached bid for %s — cannot replace", s.symbol)
        return

    lx = _lx(bid, _WATCHDOG_MULT)
    oid = await _sell_limit(s.symbol, qty, lx, "watchdog-replace")
    if oid:
        if s.t1_order_id == order_id:
            s.t1_order_id = oid
        else:
            s.runner_exit_order_id = oid
        s.exit_order_placed_at = time.monotonic()
        _start_watchdog(s, oid, qty)

    await _log.warning("PM | watchdog replace sent  %s  qty=%d  lx=%.4f  id=%s",
                       s.symbol, qty, lx, oid or "FAILED")


# ── public: price tick ────────────────────────────────────────────────────────

async def on_price_tick(
    symbol:   str,
    price:    float,
    size:     int,
    tick_dir: int = 0,
    bids:     Optional[list] = None,
    asks:     Optional[list] = None,
) -> None:
    s = _positions.get(symbol)
    if s is None or s.status not in (_Status.OPEN, _Status.CLOSING):
        return

    if size > 0:
        s._pv  += price * size
        s._v   += size
        s._p2v += price * price * size

    vwap, sigma = _vwap_sigma(s)

    minute = int(time.time()) // 60
    if minute != s.candle_minute:
        if s.candle_minute != -1 and vwap > 0 and s.candle_close < vwap and s.status == _Status.OPEN:
            await _log.warning("PM | %s 1-min close %.4f < VWAP %.4f — technical failure",
                               symbol, s.candle_close, vwap)
            bid = _cached_bid(symbol) or price * 0.999
            await _emergency_liquidate(s, bid, price, "VWAP_CANDLE")
            return
        s.candle_minute = minute
        s.candle_open   = price

    s.candle_close = price

    if s.status != _Status.OPEN or s.runner_exited:
        return

    if price > s.highest_price_seen:
        s.highest_price_seen = price

    bid = _cached_bid(symbol) or price * 0.999

    if s.entry_price > 0:
        if (price - s.entry_price) * _held_qty(s) <= -MAX_RISK_PER_TRADE:
            await _log.error("PM | %s floating loss hit −$%.2f", symbol, MAX_RISK_PER_TRADE)
            await _emergency_liquidate(s, bid, price, "MAX_LOSS")
            return

    # ── L2 / T&S pressure ────────────────────────────────────────────────────
    if bids and asks:
        bd = sum(sz for _, sz in bids[:3])
        ad = sum(sz for _, sz in asks[:3])
        large_sell = size >= _L2_MILD_SELL_SIZE and tick_dir == -1

        if ad >= _L2_SEVERE_ASK_RATIO * bd and large_sell:
            await _log.error(
                "PM | %s L2/T&S severe pressure  ask_depth=%d  bid_depth=%d  sell=%d",
                symbol, ad, bd, size,
            )
            await _emergency_liquidate(s, bid, price, "L2_TS_SEVERE")
            return

        if (ad > bd or large_sell) and s.trail_pct > _TIGHT_TRAIL_PCT:
            s.trail_pct = _TIGHT_TRAIL_PCT
            await _log.warning(
                "PM | %s trail tightened → %.1f%%  (L2/T&S mild  ask_depth=%d  bid_depth=%d)",
                symbol, _TIGHT_TRAIL_PCT * 100, ad, bd,
            )

    if price <= s.highest_price_seen * (1 - s.trail_pct):
        await _log.info("PM | %s trail stop  price=%.4f  high=%.4f  trail=%.1f%%",
                        symbol, price, s.highest_price_seen, s.trail_pct * 100)
        await _exit_runner(s, bid, price, "TRAIL_STOP")
        return

    if s.break_even_active and price <= s.stop_price:
        await _log.info("PM | %s BE stop  price=%.4f  be=%.4f", symbol, price, s.stop_price)
        await _exit_runner(s, bid, price, "BE_STOP")
        return

    if vwap > 0 and sigma > 0:
        exhaustion = vwap + _VWAP_STD_BANDS * sigma
        if price >= exhaustion:
            await _log.info("PM | %s VWAP exhaustion  price=%.4f  band=%.4f", symbol, price, exhaustion)
            await _exit_runner(s, bid, price, "VWAP_EXHAUSTION")


# ── trade_updates WebSocket ───────────────────────────────────────────────────

async def handle_trade_update(msg: dict) -> None:
    event    = msg.get("event")
    order    = msg.get("order", {})
    order_id = str(order.get("id", ""))
    symbol   = str(order.get("symbol", ""))
    side     = str(order.get("side", ""))

    s = _positions.get(symbol)
    if s is None:
        return

    try:
        fill_px  = float(order.get("filled_avg_price") or 0)
        fill_qty = int(float(order.get("filled_qty") or 0))
    except (ValueError, TypeError):
        fill_px, fill_qty = 0.0, 0

    if event == "fill" and side == "buy":
        if order_id == s.entry_order_id and s.status == _Status.PENDING_ENTRY:
            await _on_entry_fill(s, fill_px, fill_qty)

    elif event == "partial_fill" and side == "buy":
        await _log.info("PM | %s entry partial fill  qty=%d  px=%.4f", symbol, fill_qty, fill_px)

    elif event == "fill" and side == "sell":
        if order_id == s.t1_order_id and not s.t1_filled:
            _cancel_watchdog(s)
            await _on_t1_fill(s, fill_px)
        elif order_id == s.runner_exit_order_id and not s.runner_exited:
            _cancel_watchdog(s)
            await _on_runner_fill(s, fill_px)

    elif event == "canceled":
        await _log.info("PM | %s order cancelled  id=%s", symbol, order_id)


async def run_trade_updates() -> None:
    """
    Persistent Alpaca trade_updates stream.
    Wire into main.py as an asyncio.Task alongside Track-A and Track-B.
    Reconnects with exponential backoff on any connection failure.
    """
    backoff = 1
    while True:
        try:
            async with websockets.connect(
                _TRADE_UPDATES_URL, ping_interval=20, ping_timeout=10
            ) as ws:
                backoff = 1
                await ws.send(json.dumps({
                    "action": "authenticate",
                    "data":   {"key_id": ALPACA_API_KEY, "secret_key": ALPACA_SECRET_KEY},
                }))
                resp = json.loads(await ws.recv())
                if resp.get("data", {}).get("status") != "authorized":
                    raise ConnectionError(f"trade_updates auth failed: {resp}")
                await _log.info("PM | trade_updates authenticated")

                await ws.send(json.dumps({
                    "action": "listen",
                    "data":   {"streams": ["trade_updates"]},
                }))

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        if msg.get("stream") == "trade_updates":
                            asyncio.create_task(
                                handle_trade_update(msg.get("data", {})),
                                name=f"tu-{msg.get('data', {}).get('order', {}).get('id', 'x')}",
                            )
                    except (json.JSONDecodeError, TypeError):
                        continue

        except asyncio.CancelledError:
            await _log.info("PM | trade_updates cancelled — shutting down")
            raise
        except Exception as exc:
            await _log.error("PM | trade_updates lost (%s: %s) — retry in %ds",
                             type(exc).__name__, exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ── EOD force-close ───────────────────────────────────────────────────────────

async def eod_close_all() -> None:
    """Cancel all open orders and exit every open position at market-clearing limit."""
    await _log.warning("PM | EOD force-close — liquidating %d position(s)", len(_positions))
    for s in list(_positions.values()):
        bid = _cached_bid(s.symbol) or (s.entry_price * 0.99 if s.entry_price else 0)
        if bid <= 0:
            continue
        await _emergency_liquidate(s, bid, bid, "EOD")


async def run_eod_guardian() -> None:
    """Sleep until 3:55 PM ET then force-close all legs. Add as asyncio.Task in main.py."""
    now    = datetime.now(tz=_ET)
    target = datetime.combine(now.date(), _EOD_TIME, tzinfo=_ET)
    if target > now:
        await asyncio.sleep((target - now).total_seconds())
    await eod_close_all()
