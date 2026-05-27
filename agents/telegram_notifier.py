"""Telegram Bot API notifier for trade alerts."""

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


async def send_entry_alert(symbol: str, price: float, shares: int, cost: float) -> None:
    await _send(
        f"<b>ENTRY</b> {symbol}\n"
        f"${price:.4f}  x{shares}sh  cost=${cost:.2f}"
    )


async def send_exit_alert(
    *,
    symbol: str,
    exit_price: float,
    reason: str,
    entry_price: float,
    pnl: float,
    rvol: float,
    t1_filled: bool,
    t1_fill_price: float,
    t1_shares: int,
    runner_shares: int,
) -> None:
    sign = "+" if pnl >= 0 else ""
    t1_line = f"T1: {t1_shares}sh @ ${t1_fill_price:.4f}\n" if t1_filled else ""
    await _send(
        f"<b>EXIT ({reason})</b> {symbol}\n"
        f"Entry ${entry_price:.4f} → Exit ${exit_price:.4f}\n"
        f"{t1_line}"
        f"Runner: {runner_shares}sh\n"
        f"P&amp;L: <b>{sign}${pnl:.2f}</b>  RVOL: {rvol:.1f}x"
    )
