"""Telegram Bot API notifier for swing trade alerts."""

from __future__ import annotations

import logging

import aiohttp

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

_log = logging.getLogger(__name__)
_TIMEOUT = aiohttp.ClientTimeout(total=5)


async def _send(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    _log.warning("Telegram | HTTP %d", resp.status)
    except Exception as exc:
        _log.warning("Telegram | send failed: %s", exc)


async def send_entry_alert(
    symbol: str,
    price: float,
    shares: int,
    cost: float,
    strategy_id: str = "",
    catalyst_type: str = "",
) -> None:
    tag = f" [{strategy_id}]" if strategy_id else ""
    catalyst = f"\nCatalyst: {catalyst_type}" if catalyst_type else ""
    await _send(
        f"<b>ENTRY{tag}</b> {symbol}\n"
        f"${price:.4f} x{shares}sh cost=${cost:.2f}"
        f"{catalyst}"
    )


async def send_exit_alert(
    *,
    symbol: str,
    exit_price: float,
    reason: str,
    entry_price: float,
    pnl: float,
    rvol: float = 0.0,
    t1_filled: bool = False,
    t1_fill_price: float = 0.0,
    t1_shares: int = 0,
    runner_shares: int = 0,
    strategy_id: str = "",
    catalyst_type: str = "",
) -> None:
    sign = "+" if pnl >= 0 else ""
    tag = f" [{strategy_id}]" if strategy_id else ""
    catalyst = f"\nCatalyst: {catalyst_type}" if catalyst_type else ""
    t1_line = f"T1: {t1_shares}sh @ ${t1_fill_price:.4f}\n" if t1_filled else ""
    await _send(
        f"<b>EXIT{tag} ({reason})</b> {symbol}\n"
        f"Entry ${entry_price:.4f} -> Exit ${exit_price:.4f}\n"
        f"{t1_line}"
        f"Runner: {runner_shares}sh\n"
        f"P&amp;L: <b>{sign}${pnl:.2f}</b> RVOL: {rvol:.1f}x"
        f"{catalyst}"
    )


async def send_daily_summary(summary: str) -> None:
    await _send(f"<b>Daily Swing Summary</b>\n{summary}")
