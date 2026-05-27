"""
Smoke test: all modules import cleanly and critical settings are correct.
Does not connect to any external service.
"""
import importlib

import pytest


_MODULES = [
    "config.settings",
    "config.market_hours",
    "config",
    "strategies.tier1_universe_sweep",
    "strategies.tier2_baseline_calc",
    "strategies.trackA_volume_scout",
    "execution.trackB_realtime",
    "execution.position_manager",
    "agents.news_analyst",
    "agents.telegram_notifier",
    "agents.premarket_news",
]


@pytest.mark.parametrize("module", _MODULES)
def test_module_imports(module):
    importlib.import_module(module)


def test_data_feed_is_sip():
    from config import settings
    assert settings.ALPACA_DATA_FEED == "sip"


def test_ws_url_uses_sip():
    from config import settings
    assert settings.ALPACA_WS_URL.endswith("/sip")


def test_risk_controls_are_positive():
    from config import settings
    assert settings.MAX_POSITION_COST > 0
    assert settings.MAX_RISK_PER_TRADE > 0
    assert settings.MAX_RISK_PER_TRADE < settings.MAX_POSITION_COST


def test_market_hours_session_state():
    from config.market_hours import SessionState, current_session
    session = current_session()
    assert isinstance(session, SessionState)


def test_config_shared_state_initialized():
    import config
    assert isinstance(config.hot_watchlist, list)
    assert isinstance(config.blocked_tickers, set)
