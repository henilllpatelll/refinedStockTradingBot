from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import time
from typing import Any

import requests

from config.settings import FINNHUB_API_KEY, FINNHUB_BASE_URL

_TECH = {
    "technology",
    "semiconductors",
    "software",
    "electronic technology",
    "information technology services",
    "communications equipment",
}
_COMMUNICATION = {"communication services", "media", "entertainment", "telecommunications"}
_CONSUMER_CYCLICAL = {"consumer cyclical", "retail trade", "consumer durables", "autos"}
_CONSUMER_STAPLES = {"consumer defensive", "consumer non-durables", "food", "beverages"}
_ENERGY = {"energy", "oil", "gas", "energy minerals"}
_FINANCIAL = {"financial", "banks", "bank", "insurance", "capital markets", "finance"}
_HEALTHCARE = {"health", "healthcare", "biotechnology", "pharmaceuticals", "medical"}
_INDUSTRIAL = {"industrial", "industrials", "transportation", "aerospace", "producer manufacturing"}
_MATERIALS = {"basic materials", "materials", "chemicals", "non-energy minerals"}
_REAL_ESTATE = {"real estate", "reit"}
_UTILITIES = {"utilities", "utility"}


def finnhub_get(
    endpoint: str,
    params: dict[str, Any] | None = None,
    *,
    api_key: str | None = FINNHUB_API_KEY,
    timeout: float = 20.0,
) -> Any:
    if not api_key:
        raise RuntimeError("FINNHUB_API_KEY is not configured")
    request_params = dict(params or {})
    request_params["token"] = api_key
    try:
        response = requests.get(f"{FINNHUB_BASE_URL}{endpoint}", params=request_params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        status = status_code or getattr(locals().get("response", None), "status_code", "unknown")
        raise RuntimeError(f"Finnhub request failed: HTTP {status} for {endpoint}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Finnhub request failed for {endpoint}: {type(exc).__name__}") from exc


def normalize_earnings_calendar(payload: dict, symbols: list[str] | None = None) -> list[dict]:
    allowed = {symbol.upper() for symbol in symbols or []}
    events: list[dict] = []
    for item in payload.get("earningsCalendar", []) or []:
        symbol = str(item.get("symbol", "")).upper()
        if not symbol or (allowed and symbol not in allowed):
            continue
        events.append(
            {
                "symbol": symbol,
                "date": item.get("date"),
                "hour": item.get("hour"),
                "eps_estimate": item.get("epsEstimate"),
                "eps_actual": item.get("epsActual"),
                "revenue_estimate": item.get("revenueEstimate"),
                "revenue_actual": item.get("revenueActual"),
                "source": "finnhub",
            }
        )
    return events


def fetch_earnings_calendar(
    start: date,
    end: date,
    symbols: list[str] | None = None,
) -> list[dict]:
    payload = finnhub_get(
        "/calendar/earnings",
        {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "international": "false",
        },
    )
    return normalize_earnings_calendar(payload, symbols=symbols)


def _contains_any(value: str, needles: set[str]) -> bool:
    lowered = value.lower()
    return any(needle in lowered for needle in needles)


def sector_etf_for_profile(profile: dict) -> str | None:
    industry = str(profile.get("finnhubIndustry") or profile.get("sector") or "")
    if not industry:
        return None
    if _contains_any(industry, _TECH):
        return "XLK"
    if _contains_any(industry, _COMMUNICATION):
        return "XLC"
    if _contains_any(industry, _CONSUMER_CYCLICAL):
        return "XLY"
    if _contains_any(industry, _CONSUMER_STAPLES):
        return "XLP"
    if _contains_any(industry, _ENERGY):
        return "XLE"
    if _contains_any(industry, _FINANCIAL):
        return "XLF"
    if _contains_any(industry, _HEALTHCARE):
        return "XLV"
    if _contains_any(industry, _INDUSTRIAL):
        return "XLI"
    if _contains_any(industry, _MATERIALS):
        return "XLB"
    if _contains_any(industry, _REAL_ESTATE):
        return "XLRE"
    if _contains_any(industry, _UTILITIES):
        return "XLU"
    return None


def build_sector_map(symbols: list[str], request_delay_seconds: float = 1.05) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for index, symbol in enumerate(symbols):
        try:
            profile = finnhub_get("/stock/profile2", {"symbol": symbol})
        except RuntimeError:
            continue
        etf = sector_etf_for_profile(profile)
        if etf:
            mapping[symbol] = etf
        if request_delay_seconds > 0 and index < len(symbols) - 1:
            time.sleep(request_delay_seconds)
    return mapping


def _parse_grade_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_positive_upgrade(item: dict) -> bool:
    action = str(item.get("action", "")).lower()
    to_grade = str(item.get("toGrade", "")).lower()
    if action in {"up", "init"}:
        return True
    return any(label in to_grade for label in ("buy", "outperform", "overweight", "positive"))


def normalize_upgrade_downgrades(records: list[dict], min_date: date, symbols: list[str] | None = None) -> list[dict]:
    allowed = {symbol.upper() for symbol in symbols or []}
    normalized: list[dict] = []
    for item in records or []:
        symbol = str(item.get("symbol", "")).upper()
        if not symbol or (allowed and symbol not in allowed):
            continue
        grade_time = _parse_grade_time(item.get("gradeTime"))
        if grade_time and grade_time.date() < min_date:
            continue
        if not _is_positive_upgrade(item):
            continue
        provider = item.get("company") or "Finnhub"
        from_grade = item.get("fromGrade") or "previous rating"
        to_grade = item.get("toGrade") or "positive rating"
        normalized.append(
            {
                "symbol": symbol,
                "catalyst_type": "analyst_upgrade",
                "headline": f"{provider} upgrades {symbol} from {from_grade} to {to_grade}",
                "detected_at": (grade_time or datetime.now(timezone.utc)).isoformat(),
                "provider": provider,
                "source": "finnhub",
                "from_grade": item.get("fromGrade"),
                "to_grade": item.get("toGrade"),
                "action": item.get("action"),
            }
        )
    return normalized


def fetch_recent_upgrades(symbols: list[str], days: int = 2) -> list[dict]:
    end = date.today()
    start = end - timedelta(days=days)
    payload = finnhub_get(
        "/stock/upgrade-downgrade",
        {"from": start.isoformat(), "to": end.isoformat()},
    )
    return normalize_upgrade_downgrades(payload or [], min_date=start, symbols=symbols)
