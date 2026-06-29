"""
Pre-market news REST poll.

At 7:00 AM ET, scan recent Alpaca news for the swing universe and feed articles
through the catalyst classifier. Blocking headlines populate config.blocked_tickers;
positive catalysts populate config.greenlighted_tickers.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

from agents.news_analyst import evaluate_news
from config.settings import (
    ALPACA_API_KEY,
    ALPACA_NEWS_URL,
    ALPACA_SECRET_KEY,
    SWING_UNIVERSE_PATH,
)

_UNIVERSE_PATH   = Path(SWING_UNIVERSE_PATH)
_LOOKBACK_HOURS  = 12
_log             = logging.getLogger(__name__)


def _load_universe() -> list[str]:
    if not _UNIVERSE_PATH.exists():
        return []
    text = _UNIVERSE_PATH.read_text().strip()
    return json.loads(text) if text else []


async def run_news_rest_scan() -> None:
    """Fetch recent news for universe symbols and mark them as catalysts."""
    symbols = _load_universe()
    if not symbols:
        _log.warning("PremarketNews | universe empty — skipping REST scan")
        return

    since = (datetime.now(tz=timezone.utc) - timedelta(hours=_LOOKBACK_HOURS)).isoformat()
    headers = {
        "APCA-API-KEY-ID":     ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    params = {
        "symbols": ",".join(symbols),
        "start":   since,
        "limit":   50,
        "sort":    "desc",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                ALPACA_NEWS_URL,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
    except Exception as exc:
        _log.error("PremarketNews | REST scan failed: %s", exc)
        return

    articles: list[dict] = data.get("news", [])
    _log.info("PremarketNews | REST scan: %d articles found", len(articles))

    if articles:
        await asyncio.gather(
            *[evaluate_news(a) for a in articles],
            return_exceptions=True,
        )
    _log.info("PremarketNews | REST scan complete")


async def run_intraday_news_rescan() -> None:
    """Lightweight news rescan for intraday catalyst updates (2-hour window)."""
    symbols = _load_universe()
    if not symbols:
        return
    since = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat()
    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    params = {"symbols": ",".join(symbols), "start": since, "limit": 20, "sort": "desc"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(ALPACA_NEWS_URL, params=params, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                data = await resp.json()
    except Exception as exc:
        _log.warning("PremarketNews | intraday rescan failed: %s", exc)
        return
    articles = data.get("news", [])
    if articles:
        await asyncio.gather(*[evaluate_news(a) for a in articles], return_exceptions=True)
    _log.info("PremarketNews | intraday rescan: %d articles", len(articles))
