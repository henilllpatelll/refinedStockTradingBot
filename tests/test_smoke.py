import importlib

import pytest


_MODULES = [
    "config.settings",
    "config.market_hours",
    "config",
    "utils.indicators",
    "strategies.tier1_universe_sweep",
    "strategies.tier2_baseline_calc",
    "strategies.eod_scanner",
    "strategies.premarket_filter",
    "strategies.watchlist_pruner",
    "strategies.swing_routine",
    "agents.earnings_calendar",
    "agents.news_analyst",
    "agents.premarket_news",
    "agents.sector_ranker",
    "agents.telegram_notifier",
    "execution.position_manager",
    "execution.swing_entry",
    "execution.trade_logger",
    "execution.strategy_analytics",
]


@pytest.mark.parametrize("module", _MODULES)
def test_module_imports(module):
    importlib.import_module(module)


def test_config_shared_state_initialized():
    import config

    assert isinstance(config.swing_watchlist, list)
    assert isinstance(config.confirmed_setups, list)
    assert isinstance(config.blocked_tickers, set)
