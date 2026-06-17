from __future__ import annotations

import logging

import config

_log = logging.getLogger(__name__)

_BLOCK_KEYWORDS = {
    "secondary_offering": ("secondary offering", "registered direct", "share offering", "public offering"),
    "bankruptcy": ("bankruptcy", "chapter 11", "going concern"),
    "delisted": ("delisting", "delisted", "nasdaq notice"),
}

_CATALYST_KEYWORDS = {
    "earnings_beat": ("beats", "beat eps", "raises guidance", "guidance raise"),
    "analyst_upgrade": ("upgrade", "price target raised", "pt raise", "initiated at buy"),
    "insider_buy": ("insider buy", "form 4", "open market purchase"),
    "fda_approval": ("fda approval", "approved by the fda", "clearance"),
    "contract_win": ("contract", "partnership", "award", "wins government"),
    "sector_tailwind": ("sector", "industry tailwind", "rotation"),
    "macro_positive": ("fed cuts", "rate cut", "strong gdp", "cooling inflation"),
}

_MACRO_KEYWORDS = ("fed", "fomc", "cpi", "ppi", "pce", "nfp", "unemployment", "gdp", "pmi", "tariff")


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
        catalyst_type = "macro_positive"
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


async def evaluate_news(payload: dict) -> dict:
    result = classify_news(payload)
    symbols = result["symbols"]

    if result["block_signal"]:
        async with config._blocked_lock:
            config.blocked_tickers.update(symbols)
        _log.warning("NewsAnalyst | BLOCK %s reason=%s", symbols, result["block_signal"])
        return result

    if result["is_macro"]:
        async with config._macro_lock:
            config.macro_alerts.append(result)

    if result["greenlight"]:
        async with config._greenlight_lock:
            config.greenlighted_tickers.update(symbols)
        _log.info("NewsAnalyst | GREENLIGHT %s catalyst=%s", symbols, result["catalyst_type"])

    return result
