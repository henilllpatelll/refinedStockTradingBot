from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import config
from config.settings import NEWS_CATALYSTS_PATH, SWING_UNIVERSE_PATH
from utils.finnhub import fetch_recent_upgrades

_log = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")
CATALYSTS_PATH = NEWS_CATALYSTS_PATH

_BLOCK_KEYWORDS = {
    "secondary_offering": ("secondary offering", "registered direct", "share offering", "public offering"),
    "bankruptcy": ("bankruptcy", "chapter 11", "going concern"),
    "delisted": ("delisting", "delisted", "nasdaq notice"),
    "dividend_cut": ("dividend cut", "suspends dividend", "eliminates dividend"),
    "management_departure": ("ceo resigns", "ceo fired", "ceo departs", "cfo resigns"),
    "customer_loss": ("loses contract", "contract terminated", "major customer"),
}

_CATALYST_KEYWORDS = {
    "earnings_beat": ("beats", "beat eps", "raises guidance", "guidance raise"),
    "analyst_upgrade": ("initiated at buy", "pt raise", "price target raised", "reiterates buy", "upgrades to buy", "raised to buy", "raised to outperform", "raised to overweight"),
    "insider_buy": ("insider buy", "form 4", "open market purchase"),
    "fda_approval": ("fda approval", "approved by the fda", "clearance"),
    "contract_win": ("contract", "partnership", "award", "wins government"),
    "sector_tailwind": ("sector", "industry tailwind", "rotation"),
    "macro_positive": ("fed cuts", "rate cut", "strong gdp", "cooling inflation"),
}

_MACRO_KEYWORDS = ("fed", "fomc", "cpi", "ppi", "pce", "nfp", "unemployment", "gdp", "pmi", "tariff")

_block_expiry: dict[str, datetime] = {}


def _text(payload: dict) -> str:
    return f"{payload.get('headline', '')} {payload.get('summary', '')}".lower()


def _first_match(text: str, mapping: dict[str, tuple[str, ...]]) -> str | None:
    for label, keywords in mapping.items():
        if any(keyword in text for keyword in keywords):
            return label
    return None


def classify_news(payload: dict) -> dict:
    text = _text(payload)
    symbols = list(dict.fromkeys(payload.get("symbols") or []))
    block_signal = _first_match(text, _BLOCK_KEYWORDS)
    catalyst_type = _first_match(text, _CATALYST_KEYWORDS)
    is_macro = any(keyword in text for keyword in _MACRO_KEYWORDS)

    if catalyst_type is None and is_macro:
        if any(kw in text for kw in ("rate cut", "fed cuts", "easing", "dovish", "pause rate")):
            catalyst_type = "macro_positive"
        elif any(kw in text for kw in ("rate hike", "rate rise", "hawkish", "tightening", "raises rates")):
            catalyst_type = "macro_headwind"
        else:
            catalyst_type = "macro_event"
    if catalyst_type is None:
        catalyst_type = "technical_breakout"

    sentiment = "negative" if block_signal else "positive" if catalyst_type != "technical_breakout" else "neutral"
    return {
        "payload": payload,
        "symbols": symbols,
        "headline": (payload.get("headline") or "").strip(),
        "greenlight": bool(symbols and not block_signal),
        "catalyst_type": catalyst_type,
        "sentiment": sentiment,
        "block_signal": block_signal,
        "is_macro": is_macro,
    }


def load_catalysts(path: str | Path = CATALYSTS_PATH) -> list[dict]:
    source = Path(path)
    if not source.exists() or source.stat().st_size == 0:
        return []
    return json.loads(source.read_text())


def save_catalysts(records: list[dict], path: str | Path = CATALYSTS_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(records, indent=2))
    return target


def record_catalyst(result: dict, path: str | Path = CATALYSTS_PATH) -> None:
    detected_at = datetime.now(_ET).isoformat()
    records = load_catalysts(path)
    payload = result.get("payload", {})
    for symbol in result.get("symbols", []):
        records.append(
            {
                "symbol": symbol,
                "catalyst_type": result.get("catalyst_type", "technical_breakout"),
                "headline": result.get("headline", ""),
                "sentiment": result.get("sentiment", "neutral"),
                "detected_at": detected_at,
                "source_url": payload.get("url"),
            }
        )
    save_catalysts(records, path)


def latest_catalysts_by_symbol(path: str | Path = CATALYSTS_PATH) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for record in load_catalysts(path):
        symbol = str(record.get("symbol", "")).upper()
        if not symbol:
            continue
        previous = latest.get(symbol)
        if previous is None or str(record.get("detected_at", "")) >= str(previous.get("detected_at", "")):
            latest[symbol] = record
    return latest


def load_universe(path: str | Path = SWING_UNIVERSE_PATH) -> list[str]:
    source = Path(path)
    if not source.exists() or source.stat().st_size == 0:
        return []
    return json.loads(source.read_text())


def append_catalyst_records(records: list[dict], path: str | Path = CATALYSTS_PATH) -> None:
    if not records:
        return
    existing = load_catalysts(path)
    existing.extend(records)
    save_catalysts(existing, path)


async def run_structured_upgrade_scan(days: int = 2) -> list[dict]:
    symbols = load_universe()
    if not symbols:
        _log.warning("NewsAnalyst | universe empty; skipping Finnhub upgrade scan")
        return []
    try:
        records = fetch_recent_upgrades(symbols, days=days)
    except RuntimeError as exc:
        _log.warning("NewsAnalyst | Finnhub upgrade scan skipped: %s", exc)
        return []
    except Exception as exc:
        _log.error("NewsAnalyst | Finnhub upgrade scan failed: %s", exc)
        return []

    append_catalyst_records(records, CATALYSTS_PATH)
    if records:
        async with config._greenlight_lock:
            config.greenlighted_tickers.update(record["symbol"] for record in records)
    _log.info("NewsAnalyst | Finnhub upgrade scan found %d record(s)", len(records))
    return records


def expire_stale_blocks(max_age_hours: int = 24) -> None:
    """Remove blocks older than max_age_hours from the in-memory set."""
    now = datetime.now(_ET)
    expired = [sym for sym, exp in _block_expiry.items() if now > exp]
    for sym in expired:
        _block_expiry.pop(sym, None)
    if expired:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_expire_blocks_async(expired))
            else:
                loop.run_until_complete(_expire_blocks_async(expired))
        except Exception:
            pass


async def _expire_blocks_async(symbols: list[str]) -> None:
    async with config._blocked_lock:
        for sym in symbols:
            config.blocked_tickers.discard(sym)


async def evaluate_news(payload: dict) -> dict:
    result = classify_news(payload)
    symbols = result["symbols"]

    if result["block_signal"]:
        async with config._blocked_lock:
            config.blocked_tickers.update(symbols)
        expiry = datetime.now(_ET) + timedelta(hours=24)
        for sym in symbols:
            _block_expiry[sym] = expiry
        _log.warning("NewsAnalyst | BLOCK %s reason=%s", symbols, result["block_signal"])
        return result

    if result["is_macro"]:
        async with config._macro_lock:
            config.macro_alerts.append(result)

    if result["greenlight"] and result["catalyst_type"] not in ("macro_event", "macro_headwind", "technical_breakout"):
        async with config._greenlight_lock:
            config.greenlighted_tickers.update(symbols)
        record_catalyst(result, CATALYSTS_PATH)
        _log.info("NewsAnalyst | GREENLIGHT %s catalyst=%s", symbols, result["catalyst_type"])

    return result
