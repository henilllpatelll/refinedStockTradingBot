from __future__ import annotations

from datetime import date

import pytest

from utils import finnhub


def test_normalize_earnings_calendar_filters_to_universe_symbols():
    payload = {
        "earningsCalendar": [
            {"symbol": "NVDA", "date": "2026-06-20", "epsEstimate": 1.25, "hour": "amc"},
            {"symbol": "MSFT", "date": "2026-06-21", "epsEstimate": 2.1, "hour": "bmo"},
        ]
    }

    events = finnhub.normalize_earnings_calendar(payload, symbols=["NVDA"])

    assert events == [
        {
            "symbol": "NVDA",
            "date": "2026-06-20",
            "hour": "amc",
            "eps_estimate": 1.25,
            "eps_actual": None,
            "revenue_estimate": None,
            "revenue_actual": None,
            "source": "finnhub",
        }
    ]


def test_sector_etf_for_profile_maps_finnhub_industry_to_spdr_etf():
    assert finnhub.sector_etf_for_profile({"finnhubIndustry": "Semiconductors"}) == "XLK"
    assert finnhub.sector_etf_for_profile({"finnhubIndustry": "Energy"}) == "XLE"
    assert finnhub.sector_etf_for_profile({"finnhubIndustry": "Banks"}) == "XLF"


def test_build_sector_map_continues_after_symbol_failure(monkeypatch):
    def fake_get(endpoint, params):
        symbol = params["symbol"]
        if symbol == "BAD":
            raise RuntimeError("provider unavailable")
        return {"finnhubIndustry": "Semiconductors" if symbol == "NVDA" else "Energy"}

    monkeypatch.setattr(finnhub, "finnhub_get", fake_get)

    mapping = finnhub.build_sector_map(["NVDA", "BAD", "SLB"], request_delay_seconds=0)

    assert mapping == {"NVDA": "XLK", "SLB": "XLE"}


def test_normalize_upgrade_downgrades_keeps_recent_positive_actions():
    records = finnhub.normalize_upgrade_downgrades(
        [
            {
                "symbol": "NVDA",
                "action": "up",
                "fromGrade": "Hold",
                "toGrade": "Buy",
                "company": "Example Bank",
                "gradeTime": "2026-06-16T12:00:00Z",
            },
            {"symbol": "NVDA", "action": "down", "toGrade": "Sell"},
        ],
        min_date=date(2026, 6, 15),
    )

    assert len(records) == 1
    assert records[0]["symbol"] == "NVDA"
    assert records[0]["catalyst_type"] == "analyst_upgrade"
    assert records[0]["provider"] == "Example Bank"


def test_finnhub_get_requires_api_key():
    with pytest.raises(RuntimeError, match="FINNHUB_API_KEY"):
        finnhub.finnhub_get("/calendar/earnings", api_key="")


def test_finnhub_get_redacts_token_from_http_errors(monkeypatch):
    class Response:
        status_code = 403
        text = "Forbidden"

        def raise_for_status(self):
            raise finnhub.requests.HTTPError(
                "403 Client Error: Forbidden for url: https://finnhub.io/api/v1/test?token=secret"
            )

        def json(self):
            return {}

    monkeypatch.setattr(finnhub.requests, "get", lambda *args, **kwargs: Response())

    with pytest.raises(RuntimeError) as excinfo:
        finnhub.finnhub_get("/test", api_key="secret")

    assert "secret" not in str(excinfo.value)
    assert "/test" in str(excinfo.value)
