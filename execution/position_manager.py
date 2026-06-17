from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, StopOrderRequest

from agents.telegram_notifier import send_entry_alert, send_exit_alert
from config.settings import ALPACA_API_KEY, ALPACA_SECRET_KEY, MAX_RISK_PER_TRADE, POSITION_STATE_PATH
from execution.trade_logger import append_trade_record
from utils.market_data import fetch_daily_bars, fetch_latest_prices
from utils.indicators import ema_last, stop_price_from_atr

_log = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")
_trading_client: TradingClient | None = None


@dataclass(frozen=True)
class ExitRule:
    t1_target_pct: float
    trail_stop_pct: float
    split_t1_pct: float = 0.5


STRATEGY_EXIT_RULES: dict[str, ExitRule] = {
    "S1": ExitRule(0.03, 0.025),
    "S2": ExitRule(0.03, 0.025),
    "S4": ExitRule(0.06, 0.04),
    "S5": ExitRule(0.04, 0.03),
    "S9": ExitRule(0.04, 0.03),
    "S12": ExitRule(0.05, 0.035),
    "S13": ExitRule(0.06, 0.04),
}


@dataclass
class PositionState:
    symbol: str
    strategy_id: str
    catalyst_type: str
    entry_price: float
    shares: int
    atr_at_entry: float
    entry_time: datetime | None = None
    highest_price_seen: float = 0.0
    t1_filled: bool = False
    t1_fill_price: float = 0.0
    remaining_shares: int = 0
    realized_pnl: float = 0.0
    protective_stop_order_id: str | None = None
    closed: bool = False

    def __post_init__(self) -> None:
        if self.remaining_shares <= 0:
            self.remaining_shares = self.shares
        if self.highest_price_seen <= 0:
            self.highest_price_seen = self.entry_price

    @property
    def stop_price(self) -> float:
        return calculate_atr_stop(self.entry_price, self.atr_at_entry)

    @property
    def t1_shares(self) -> int:
        return min(int(self.shares * STRATEGY_EXIT_RULES[self.strategy_id].split_t1_pct), self.remaining_shares)

    @property
    def runner_shares(self) -> int:
        return max(0, self.remaining_shares)


open_positions: dict[tuple[str, str], PositionState] = {}
pending_entry_orders: dict[str, dict] = {}


def _client() -> TradingClient:
    global _trading_client
    if _trading_client is None:
        _trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
    return _trading_client


def calculate_atr_stop(entry_price: float, atr_value: float) -> float:
    return stop_price_from_atr(entry_price, atr_value)


def _position_to_dict(position: PositionState) -> dict:
    data = asdict(position)
    if position.entry_time is not None:
        data["entry_time"] = position.entry_time.isoformat()
    return data


def _position_from_dict(data: dict) -> PositionState:
    raw = dict(data)
    if raw.get("entry_time"):
        raw["entry_time"] = datetime.fromisoformat(raw["entry_time"])
    return PositionState(**raw)


def save_position_state(path: str | Path = POSITION_STATE_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "open_positions": [_position_to_dict(position) for position in open_positions.values()],
        "pending_entry_orders": pending_entry_orders,
        "saved_at": datetime.now(_ET).isoformat(),
    }
    target.write_text(json.dumps(payload, indent=2))
    return target


def load_position_state(path: str | Path = POSITION_STATE_PATH) -> None:
    source = Path(path)
    open_positions.clear()
    pending_entry_orders.clear()
    if not source.exists() or source.stat().st_size == 0:
        return
    payload = json.loads(source.read_text())
    for item in payload.get("open_positions", []):
        position = _position_from_dict(item)
        open_positions[(position.symbol, position.strategy_id)] = position
    pending_entry_orders.update(payload.get("pending_entry_orders", {}))


def audit_untracked_alpaca_positions() -> list[str]:
    try:
        positions = _client().get_all_positions()
    except Exception as exc:
        _log.warning("PositionManager | Alpaca position audit failed: %s", exc)
        return []
    tracked_symbols = {symbol for symbol, _strategy_id in open_positions}
    untracked = [
        str(getattr(position, "symbol", ""))
        for position in positions
        if str(getattr(position, "symbol", "")) and str(getattr(position, "symbol", "")) not in tracked_symbols
    ]
    if untracked:
        _log.warning("PositionManager | untracked Alpaca positions found: %s", ", ".join(untracked))
    return untracked


def should_daily_close_exit(position: PositionState, daily_close: float, ema20: float) -> bool:
    return float(daily_close) < float(ema20)


def should_emergency_exit(position: PositionState, current_price: float) -> bool:
    floating_pnl = (float(current_price) - position.entry_price) * position.shares
    return floating_pnl <= -MAX_RISK_PER_TRADE


def build_daily_exit_data(bars_by_symbol) -> dict[str, dict]:
    daily_data: dict[str, dict] = {}
    for symbol, bars in bars_by_symbol.items():
        if bars is None or bars.empty or "close" not in bars:
            continue
        closes = bars["close"]
        daily_data[symbol] = {
            "close": float(closes.iloc[-1]),
            "ema20": ema_last(closes, 20) if len(closes) >= 20 else None,
        }
    return daily_data


async def fetch_daily_exit_data_for_open_positions() -> dict[str, dict]:
    symbols = list(dict.fromkeys(position.symbol for position in open_positions.values()))
    if not symbols:
        return {}
    bars_by_symbol = await fetch_daily_bars(symbols, lookback_days=60, limit=50)
    return build_daily_exit_data(bars_by_symbol)


def register_filled_entry(
    symbol: str,
    strategy_id: str,
    catalyst_type: str,
    entry_price: float,
    shares: int,
    atr_at_entry: float,
) -> PositionState:
    state = PositionState(
        symbol=symbol,
        strategy_id=strategy_id,
        catalyst_type=catalyst_type,
        entry_price=float(entry_price),
        shares=int(shares),
        atr_at_entry=float(atr_at_entry),
        entry_time=datetime.now(_ET),
        highest_price_seen=float(entry_price),
    )
    open_positions[(symbol, strategy_id)] = state
    save_position_state()
    asyncio.create_task(
        send_entry_alert(symbol, entry_price, shares, entry_price * shares, strategy_id, catalyst_type),
        name=f"tg-entry-{symbol}-{strategy_id}",
    )
    return state


async def submit_swing_entry(setup: dict, shares: int, limit_price: float) -> str | None:
    try:
        request = LimitOrderRequest(
            symbol=setup["symbol"],
            qty=shares,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=round(limit_price, 2),
            extended_hours=False,
        )
        order = await asyncio.to_thread(_client().submit_order, request)
        _log.info(
            "SwingEntry | submitted %s %s qty=%d limit=%.2f id=%s",
            setup["symbol"],
            setup["strategy_id"],
            shares,
            limit_price,
            order.id,
        )
        order_id = str(order.id)
        pending_entry_orders[order_id] = {
            **setup,
            "shares": int(shares),
            "limit_price": float(limit_price),
            "submitted_at": datetime.now(_ET).isoformat(),
        }
        save_position_state()
        return order_id
    except Exception as exc:
        _log.error("SwingEntry | order failed for %s: %s", setup.get("symbol"), exc)
        return None


async def submit_exit_order(symbol: str, shares: int, reason: str) -> str | None:
    if shares <= 0:
        return None
    try:
        request = MarketOrderRequest(
            symbol=symbol,
            qty=int(shares),
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = await asyncio.to_thread(_client().submit_order, request)
        _log.info("Exit | submitted %s qty=%d reason=%s id=%s", symbol, shares, reason, order.id)
        return str(order.id)
    except Exception as exc:
        _log.error("Exit | order failed for %s reason=%s: %s", symbol, reason, exc)
        return None


async def cancel_order(order_id: str | None) -> bool:
    if not order_id:
        return True
    try:
        await asyncio.to_thread(_client().cancel_order_by_id, order_id)
        return True
    except Exception as exc:
        _log.warning("Exit | protective stop cancel failed id=%s: %s", order_id, exc)
        return False


async def submit_protective_stop(position: PositionState, stop_price: float) -> str | None:
    if position.remaining_shares <= 0:
        return None
    try:
        request = StopOrderRequest(
            symbol=position.symbol,
            qty=int(position.remaining_shares),
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            stop_price=round(float(stop_price), 2),
        )
        order = await asyncio.to_thread(_client().submit_order, request)
        position.protective_stop_order_id = str(order.id)
        _log.info(
            "ProtectiveStop | submitted %s %s qty=%d stop=%.2f id=%s",
            position.symbol,
            position.strategy_id,
            position.remaining_shares,
            stop_price,
            order.id,
        )
        save_position_state()
        return str(order.id)
    except Exception as exc:
        _log.error("ProtectiveStop | order failed for %s: %s", position.symbol, exc)
        return None


def _order_status(order) -> str:
    status = getattr(order, "status", "")
    return str(getattr(status, "value", status)).lower()


def _filled_qty(order, fallback: int) -> int:
    raw = getattr(order, "filled_qty", None)
    if raw in (None, ""):
        return int(fallback)
    return int(float(raw))


def _filled_avg_price(order, fallback: float) -> float:
    raw = getattr(order, "filled_avg_price", None)
    if raw in (None, ""):
        return float(fallback)
    return float(raw)


async def reconcile_pending_entry_orders() -> list[tuple[str, str]]:
    filled: list[tuple[str, str]] = []
    for order_id, setup in list(pending_entry_orders.items()):
        try:
            order = await asyncio.to_thread(_client().get_order_by_id, order_id)
        except Exception as exc:
            _log.warning("PositionManager | order status failed id=%s: %s", order_id, exc)
            continue

        status = _order_status(order)
        if status in {"canceled", "expired", "rejected"}:
            pending_entry_orders.pop(order_id, None)
            save_position_state()
            _log.info("PositionManager | dropping %s order id=%s status=%s", setup.get("symbol"), order_id, status)
            continue
        if status != "filled":
            continue

        shares = _filled_qty(order, int(setup.get("shares", 0)))
        entry_price = _filled_avg_price(order, float(setup.get("limit_price", 0.0)))
        if shares <= 0 or entry_price <= 0:
            _log.warning("PositionManager | filled order missing qty/price id=%s", order_id)
            continue

        state = register_filled_entry(
            setup["symbol"],
            setup["strategy_id"],
            setup.get("catalyst_type", "technical_breakout"),
            entry_price,
            shares,
            float(setup.get("atr_14") or 0.0),
        )
        stop_order_id = await submit_protective_stop(state, state.stop_price)
        if stop_order_id:
            state.protective_stop_order_id = stop_order_id
        pending_entry_orders.pop(order_id, None)
        save_position_state()
        filled.append((state.symbol, state.strategy_id))
        _log.info("PositionManager | registered fill %s %s qty=%d price=%.2f", state.symbol, state.strategy_id, shares, entry_price)
    return filled


async def check_emergency_exits() -> list[tuple[str, str]]:
    positions = list(open_positions.values())
    prices = await fetch_latest_prices(list(dict.fromkeys(position.symbol for position in positions)))
    closed: list[tuple[str, str]] = []
    for key, position in list(open_positions.items()):
        current_price = prices.get(position.symbol)
        if current_price is None:
            continue
        actions = await apply_price_exit_rules(position, current_price)
        if position.closed and actions:
            closed.append(key)
    return closed


async def apply_price_exit_rules(position: PositionState, current_price: float) -> list[str]:
    current_price = float(current_price)
    position.highest_price_seen = max(position.highest_price_seen, current_price)
    rule = STRATEGY_EXIT_RULES[position.strategy_id]

    if current_price <= position.stop_price:
        await close_position(position, current_price, "STOP_LOSS")
        return ["STOP_LOSS"]
    if should_emergency_exit(position, current_price):
        await close_position(position, current_price, "EMERGENCY_MAX_LOSS")
        return ["EMERGENCY_MAX_LOSS"]

    t1_price = position.entry_price * (1 + rule.t1_target_pct)
    if not position.t1_filled and current_price >= t1_price:
        shares = position.t1_shares
        await cancel_order(position.protective_stop_order_id)
        position.protective_stop_order_id = None
        order_id = await submit_exit_order(position.symbol, shares, "T1_TARGET")
        if order_id:
            position.t1_filled = True
            position.t1_fill_price = current_price
            position.remaining_shares -= shares
            position.realized_pnl += (current_price - position.entry_price) * shares
            if position.remaining_shares > 0:
                await submit_protective_stop(position, position.entry_price)
            save_position_state()
            return ["T1_TARGET"]
        return []

    if position.t1_filled and position.remaining_shares > 0:
        trailing_stop = position.highest_price_seen * (1 - rule.trail_stop_pct)
        runner_stop = max(position.entry_price, trailing_stop)
        if current_price <= runner_stop:
            await close_position(position, current_price, "TRAILING_STOP")
            return ["TRAILING_STOP"]

    return []


async def close_position(position: PositionState, exit_price: float, reason: str) -> None:
    shares_to_close = position.remaining_shares if position.remaining_shares > 0 else position.shares
    await cancel_order(position.protective_stop_order_id)
    position.protective_stop_order_id = None
    order_id = await submit_exit_order(position.symbol, shares_to_close, reason)
    if not order_id:
        return
    position.closed = True
    open_positions.pop((position.symbol, position.strategy_id), None)
    pnl = round(position.realized_pnl + (float(exit_price) - position.entry_price) * shares_to_close, 2)
    position.remaining_shares = 0
    save_position_state()
    hold_days = 0
    if position.entry_time is not None:
        hold_days = max(0, (datetime.now(_ET).date() - position.entry_time.date()).days)
    append_trade_record(
        {
            "symbol": position.symbol,
            "strategy_id": position.strategy_id,
            "catalyst_type": position.catalyst_type,
            "entry_price": position.entry_price,
            "exit_price": float(exit_price),
            "pnl": pnl,
            "hold_days": hold_days,
            "exit_reason": reason,
            "signal_strength": 1,
        }
    )
    await send_exit_alert(
        symbol=position.symbol,
        exit_price=float(exit_price),
        reason=reason,
        entry_price=position.entry_price,
        pnl=pnl,
        t1_filled=position.t1_filled,
        t1_fill_price=position.t1_fill_price,
        t1_shares=position.t1_shares,
        runner_shares=position.runner_shares,
        strategy_id=position.strategy_id,
        catalyst_type=position.catalyst_type,
    )


async def eod_close_check(daily_data_by_symbol: dict[str, dict]) -> list[tuple[str, str]]:
    closed: list[tuple[str, str]] = []
    for key, position in list(open_positions.items()):
        daily = daily_data_by_symbol.get(position.symbol, {})
        close = daily.get("close")
        ema20 = daily.get("ema20")
        if close is None or ema20 is None:
            continue
        if should_daily_close_exit(position, close, ema20):
            await close_position(position, close, "DAILY_CLOSE_BELOW_EMA20")
            closed.append(key)
    return closed


async def run_trade_updates(poll_interval_seconds: float = 15.0) -> None:
    _log.info("PositionManager | trade update stream placeholder active")
    while True:
        await reconcile_pending_entry_orders()
        await check_emergency_exits()
        await asyncio.sleep(poll_interval_seconds)
