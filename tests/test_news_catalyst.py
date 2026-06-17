import pytest

import config
from agents.news_analyst import classify_news, evaluate_news


@pytest.fixture(autouse=True)
def reset_news_state():
    config.greenlighted_tickers.clear()
    config.blocked_tickers.clear()
    config.macro_alerts.clear()
    yield
    config.greenlighted_tickers.clear()
    config.blocked_tickers.clear()
    config.macro_alerts.clear()


def test_classify_news_tags_earnings_beat():
    result = classify_news(
        {
            "headline": "Example beats EPS estimates and raises guidance",
            "summary": "Revenue topped consensus.",
            "symbols": ["EXMP"],
        }
    )

    assert result["catalyst_type"] == "earnings_beat"
    assert result["sentiment"] == "positive"
    assert result["block_signal"] is None


def test_classify_news_blocks_secondary_offerings():
    result = classify_news({"headline": "Example announces secondary offering", "symbols": ["EXMP"]})

    assert result["greenlight"] is False
    assert result["block_signal"] == "secondary_offering"


def test_classify_news_detects_macro_events_without_symbol_requirement():
    result = classify_news({"headline": "Fed cuts rates after FOMC decision", "symbols": []})

    assert result["is_macro"] is True
    assert result["catalyst_type"] == "macro_positive"


@pytest.mark.asyncio
async def test_evaluate_news_greenlights_symbols_with_catalyst_type():
    result = await evaluate_news(
        {
            "headline": "Example wins government contract",
            "summary": "Large partnership announced.",
            "symbols": ["EXMP"],
        }
    )

    assert result["greenlight"] is True
    assert result["catalyst_type"] == "contract_win"
    assert "EXMP" in config.greenlighted_tickers


@pytest.mark.asyncio
async def test_evaluate_news_adds_blocked_tickers_for_block_signal():
    result = await evaluate_news({"headline": "Example files for bankruptcy", "symbols": ["EXMP"]})

    assert result["greenlight"] is False
    assert result["block_signal"] == "bankruptcy"
    assert "EXMP" in config.blocked_tickers
