import asyncio

# Shared swing pipeline state.
swing_watchlist: list[dict] = []
confirmed_setups: list[dict] = []
blocked_tickers: set[str] = set()
greenlighted_tickers: set[str] = set()
macro_alerts: list[dict] = []

_watchlist_lock = asyncio.Lock()
_confirmed_lock = asyncio.Lock()
_blocked_lock = asyncio.Lock()
_greenlight_lock = asyncio.Lock()
_macro_lock = asyncio.Lock()

# Compatibility aliases for legacy modules during the rebuild.
hot_watchlist = swing_watchlist
premarket_catalyst_watchlist: set[str] = set()
_premarket_catalyst_lock = asyncio.Lock()
_premarket_catalyst_event = asyncio.Event()
_symbol_cooldown: dict[str, dict] = {}
_cooldown_lock = asyncio.Lock()
