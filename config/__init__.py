import asyncio

# Shared in-process state written by Track-A, read by Track-B.
# Both tracks run inside the same asyncio event loop, so asyncio.Lock
# is the correct primitive — no threading overhead needed.
hot_watchlist: list[dict] = []
_watchlist_lock = asyncio.Lock()

# News Analyst writes here; Position Manager checks before every entry.
blocked_tickers: set[str] = set()
_blocked_lock = asyncio.Lock()

# News Analyst writes here when a valid catalyst is confirmed; Position Manager
# requires membership before placing any entry order.
greenlighted_tickers: set[str] = set()
_greenlight_lock = asyncio.Lock()
