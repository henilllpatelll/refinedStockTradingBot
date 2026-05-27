"""
Tier-1 Universe Sweep — scheduled for 3:30 AM ET.

Primary path  : Finviz screener (requests + BeautifulSoup).
Fallback path : Alpha Vantage OVERVIEW on a hard-coded seed list.
Output        : config/low_float_universe.json
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config.settings import (
    ALPHA_VANTAGE_API_KEY,
    ALPHA_VANTAGE_BASE_URL,
    FINVIZ_SCREENER_BASE_URL,
    MAX_UNIVERSE_SIZE,
)

_UNIVERSE_PATH = Path(__file__).parent.parent / "config" / "low_float_universe.json"

# ── screening thresholds ────────────────────────────────────────────────────
_MAX_MARKET_CAP = 2_000_000_000   # $2 B
_MIN_PRICE      = 1.00
_MAX_PRICE      = 20.00
_MAX_FLOAT      = 20_000_000      # 20 M shares

# ── network constants ───────────────────────────────────────────────────────
_FINVIZ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_FINVIZ_PAGE_DELAY = 1.5          # seconds between Finviz page requests
_AV_RATE_LIMIT    = 13.0          # seconds between AV calls (free: 5 req/min)

# ── Alpha Vantage fallback seed (known low-float candidates) ────────────────
_FALLBACK_SEED: list[str] = [
    "AEHR", "ALDX", "AMTX", "ANTE", "APLD", "APVO", "AREB",
    "ATGL", "ATXI", "AVGR", "BFRI", "BHAT", "BNRG", "BURU",
    "CERO", "CKPT", "CLNN", "CMPX", "CODA", "COEP", "CRVS",
    "CTCX", "DATS", "DAVE", "DBGI", "DCGO", "DFLI", "DPSI",
    "ELEV", "ENVB", "EVTL", "FEMY", "FFIE", "FGEN", "FRST",
    "GOVX", "HPNN", "INPX", "ISIG", "KAVL", "KPLT", "LMND",
]


# ── public helper (also used by other modules) ──────────────────────────────

def parse_finviz_number(raw: str) -> Optional[int]:
    """Convert a Finviz-formatted number string to a plain integer.

    Handles suffixes K / M / B / T and comma-separated thousands.
    Returns None for missing or non-numeric values ('-', 'N/A', '').

    Examples
    --------
    >>> parse_finviz_number('15.4M')   # 15_400_000
    >>> parse_finviz_number('850K')    # 850_000
    >>> parse_finviz_number('1.2B')    # 1_200_000_000
    >>> parse_finviz_number('-')       # None
    """
    if not raw:
        return None
    cleaned = raw.strip().upper().replace(",", "")
    if cleaned in ("-", "N/A", ""):
        return None
    _suffixes = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "T": 1_000_000_000_000}
    m = re.match(r"^(\d+\.?\d*)([KMBT]?)$", cleaned)
    if not m:
        return None
    return int(float(m.group(1)) * _suffixes.get(m.group(2), 1))


# ── Finviz scraper ──────────────────────────────────────────────────────────

def _detect_columns(header_row) -> dict[str, int]:
    """Return {header_text: column_index} for a Finviz header <tr>."""
    # Finviz now uses <th> elements for headers (previously <td>).
    elements = header_row.find_all("th") or header_row.find_all("td")
    return {el.get_text(strip=True): i for i, el in enumerate(elements)}


def _parse_screener_page(html: str) -> list[dict]:
    """Parse one Finviz screener page; raise ValueError on layout change."""
    soup = BeautifulSoup(html, "html.parser")

    # Finviz now renders multiple tables per page (filter UI + results).
    # Identify the results table: has a <th> header with "Ticker"/"Market Cap"
    # AND contains <td> rows where the Ticker column holds a valid ticker symbol.
    results_table = None
    col: dict[str, int] = {}
    for table in soup.find_all("table"):
        header_row = None
        for tr in table.find_all("tr"):
            ths = tr.find_all("th")
            if ths and "Ticker" in {th.get_text(strip=True) for th in ths}:
                header_row = tr
                break
        if header_row is None:
            continue
        candidate_col = _detect_columns(header_row)
        tidx = candidate_col.get("Ticker")
        if tidx is None:
            continue
        # Confirm at least one data row has a valid ticker in the Ticker column.
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) > tidx and re.match(r"^[A-Z]{1,5}$", tds[tidx].get_text(strip=True)):
                results_table = table
                col = candidate_col
                break
        if results_table:
            break

    if not results_table or not col:
        raise ValueError("Finviz layout changed: header row not found")

    for required in ("Ticker", "Market Cap", "Price"):
        if required not in col:
            raise ValueError(f"Finviz layout changed: '{required}' column absent")

    ticker_idx = col["Ticker"]
    mc_idx     = col["Market Cap"]
    price_idx  = col["Price"]

    rows: list[dict] = []
    for tr in results_table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) <= max(ticker_idx, mc_idx, price_idx):
            continue

        ticker = tds[ticker_idx].get_text(strip=True)
        if not re.match(r"^[A-Z]{1,5}$", ticker):
            continue

        market_cap = parse_finviz_number(tds[mc_idx].get_text(strip=True))
        try:
            price = float(tds[price_idx].get_text(strip=True).replace(",", ""))
        except ValueError:
            price = None

        rows.append({"ticker": ticker, "market_cap": market_cap, "price": price})
    return rows


def _apply_filters(rows: list[dict]) -> list[str]:
    passed: list[str] = []
    for row in rows:
        mc, price = row["market_cap"], row["price"]
        if mc is None or price is None:
            continue
        if mc > _MAX_MARKET_CAP:
            continue
        if not (_MIN_PRICE <= price <= _MAX_PRICE):
            continue
        passed.append(row["ticker"])
        if len(passed) >= MAX_UNIVERSE_SIZE:
            break
    return passed


def _scrape_finviz() -> list[str]:
    all_rows: list[dict] = []
    offset = 1
    while len(all_rows) < MAX_UNIVERSE_SIZE * 4:
        url = f"{FINVIZ_SCREENER_BASE_URL}&r={offset}"
        resp = requests.get(url, headers=_FINVIZ_HEADERS, timeout=20)
        resp.raise_for_status()
        page_rows = _parse_screener_page(resp.text)
        if not page_rows:
            break
        all_rows.extend(page_rows)
        if len(page_rows) < 20:
            break  # last page reached
        offset += 20
        time.sleep(_FINVIZ_PAGE_DELAY)

    return _apply_filters(all_rows)


# ── Alpha Vantage fallback ──────────────────────────────────────────────────

def _av_overview(symbol: str) -> Optional[dict]:
    resp = requests.get(
        ALPHA_VANTAGE_BASE_URL,
        params={
            "function": "OVERVIEW",
            "symbol":   symbol,
            "apikey":   ALPHA_VANTAGE_API_KEY,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if "Symbol" in data else None


def _alpha_vantage_fallback() -> list[str]:
    log = logging.getLogger(__name__)
    passed: list[str] = []

    for symbol in _FALLBACK_SEED:
        if len(passed) >= MAX_UNIVERSE_SIZE:
            break
        try:
            data = _av_overview(symbol)
            if not data:
                continue

            try:
                mc = int(data.get("MarketCapitalization") or 0)
            except (ValueError, TypeError):
                mc = 0

            try:
                shares = int(data.get("SharesOutstanding") or 0)
            except (ValueError, TypeError):
                shares = 0

            try:
                week_high = float(data.get("52WeekHigh") or 0)
                week_low  = float(data.get("52WeekLow")  or 0)
                mid_price = (week_high + week_low) / 2 if week_high else 0
            except (ValueError, TypeError):
                mid_price = 0

            if mc == 0 or mc > _MAX_MARKET_CAP:
                continue
            # SharesOutstanding is a conservative proxy for float in the fallback path
            if shares > _MAX_FLOAT:
                continue
            if mid_price and not (_MIN_PRICE <= mid_price <= _MAX_PRICE):
                continue

            passed.append(symbol)
            log.info("AV fallback accepted  %s  mc=%s  shares=%s", symbol, mc, shares)

        except Exception as exc:
            log.warning("AV OVERVIEW failed for %s: %s", symbol, exc)

        time.sleep(_AV_RATE_LIMIT)

    return passed


# ── public entry point ──────────────────────────────────────────────────────

def run_universe_sweep() -> list[str]:
    """Scrape universe, apply filters, persist to JSON. Returns ticker list."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    log = logging.getLogger(__name__)

    tickers: list[str] = []
    try:
        log.info("Tier-1 | scraping Finviz")
        tickers = _scrape_finviz()
        log.info("Tier-1 | Finviz → %d tickers", len(tickers))
    except Exception as exc:
        log.warning("Tier-1 | Finviz failed (%s) — activating Alpha Vantage fallback", exc)
        tickers = _alpha_vantage_fallback()
        log.info("Tier-1 | AV fallback → %d tickers", len(tickers))

    if not tickers:
        log.error("Tier-1 | zero tickers returned; skipping JSON save")
        return []

    _UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _UNIVERSE_PATH.write_text(json.dumps(tickers, indent=2))
    log.info("Tier-1 | saved %d tickers → %s", len(tickers), _UNIVERSE_PATH)
    return tickers
