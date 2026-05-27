"""
Pre-market news filter.

Tier-3 (~3:50 AM ET): REST scan of the last 12 hours of news for all universe
                       symbols via the Alpaca news API; runs every article through
                       evaluate_news() to pre-block bad tickers before open.
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
)

_UNIVERSE_PATH   = Path(__file__).parent.parent / "config" / "low_float_universe.json"
_LOOKBACK_HOURS  = 12
_log             = logging.getLogger(__name__)


def _load_universe() -> list[str]:
    if not _UNIVERSE_PATH.exists():
        return []
    text = _UNIVERSE_PATH.read_text().strip()
    return json.loads(text) if text else []


async def run_news_rest_scan() -> None:
    """Fetch recent news for universe symbols and pre-block any bad tickers."""
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
