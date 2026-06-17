from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest

from agents.telegram_notifier import send_entry_alert, send_exit_alert
from config.settings import ALPACA_API_KEY, ALPACA_SECRET_KEY, MAX_RISK_PER_TRADE
from execution.trade_logger import append_trade_record
from utils.indicators import stop_price_from_atr

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
    closed: bool = False

    @property
    def stop_price(self) -> float:
        return calculate_atr_stop(self.entry_price, self.atr_at_entry)

    @property
    def t1_shares(self) -> int:
        return int(self.shares * STRATEGY_EXIT_RULES[self.strategy_id].split_t1_pct)

    @property
    def runner_shares(self) -> int:
        return self.shares - self.t1_shares


open_positions: dict[tuple[str, str], PositionState] = {}


def _client() -> TradingClient:
    global _trading_client
    if _trading_client is None:
        _trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
    return _trading_client


def calculate_atr_stop(entry_price: float, atr_value: float) -> float:
    return stop_price_from_atr(entry_price, atr_value)


def should_daily_close_exit(position: PositionState, daily_close: float, ema20: float) -> bool:
    return float(daily_close) < float(ema20)


def should_emergency_exit(position: PositionState, current_price: float) -> bool:
    floating_pnl = (float(current_price) - position.entry_price) * position.shares
    return floating_pnl <= -MAX_RISK_PER_TRADE


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
        return str(order.id)
    except Exception as exc:
        _log.error("SwingEntry | order failed for %s: %s", setup.get("symbol"), exc)
        return None


async def close_position(position: PositionState, exit_price: float, reason: str) -> None:
    position.closed = True
    open_positions.pop((position.symbol, position.strategy_id), None)
    pnl = round((float(exit_price) - position.entry_price) * position.shares, 2)
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


async def run_trade_updates() -> None:
    _log.info("PositionManager | trade update stream placeholder active")
    while True:
        await asyncio.sleep(3600)
