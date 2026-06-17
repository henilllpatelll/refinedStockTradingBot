import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Alpaca Paper Trading — credentials loaded from .env, never hardcoded
# ---------------------------------------------------------------------------
ALPACA_API_KEY: str = os.environ["ALPACA_PAPER_API_KEY"]
ALPACA_SECRET_KEY: str = os.environ["ALPACA_PAPER_SECRET_KEY"]
ALPACA_BASE_URL: str = "https://paper-api.alpaca.markets"
ALPACA_DATA_URL: str = "https://data.alpaca.markets"

# ---------------------------------------------------------------------------
# Risk controls
# ---------------------------------------------------------------------------
# Maximum gross cost basis allowed for any single open position (USD)
MAX_POSITION_COST: float = 1000.0

# Maximum dollar loss tolerated on any single trade before hard exit (USD)
MAX_RISK_PER_TRADE: float = 40.0

# ---------------------------------------------------------------------------
# Universe sweep parameters (Tier-1, Saturday 8:00 AM ET)
# ---------------------------------------------------------------------------
FINVIZ_SCREENER_BASE_URL: str = (
    "https://finviz.com/screener.ashx"
    "?v=111&f=sh_avgvol_o500,sh_price_o2&ft=4&o=-volume"
)
MAX_UNIVERSE_SIZE: int = 1000        # cap symbols carried into Tier-2; Alpaca batch snapshot handles up to 1000
MIN_SWING_PRICE: float = 2.0
MIN_AVG_DAILY_VOLUME: int = 500_000
SWING_UNIVERSE_PATH: str = "config/swing_universe.json"
SWING_WATCHLIST_PATH: str = "config/swing_watchlist.json"
CONFIRMED_SETUPS_PATH: str = "config/confirmed_setups.json"
EARNINGS_CALENDAR_PATH: str = "config/earnings_calendar.json"
SECTOR_RANKINGS_PATH: str = "config/sector_rankings.json"
SECTOR_MAP_PATH: str = "config/sector_map.json"
NEWS_CATALYSTS_PATH: str = "config/news_catalysts.json"
POSITION_STATE_PATH: str = "config/position_state.json"

# ---------------------------------------------------------------------------
# Alpha Vantage (Tier-1 fallback)
# ---------------------------------------------------------------------------
ALPHA_VANTAGE_API_KEY: str = os.getenv("ALPHA_VANTAGE_API_KEY", "demo")
ALPHA_VANTAGE_BASE_URL: str = "https://www.alphavantage.co/query"

# ---------------------------------------------------------------------------
# Finnhub fundamentals/events provider
# ---------------------------------------------------------------------------
FINNHUB_API_KEY: str = os.getenv("FINNHUB_API_KEY", "")
FINNHUB_BASE_URL: str = "https://finnhub.io/api/v1"

# ---------------------------------------------------------------------------
# Baseline calculation parameters (Tier-2, 3:45 PM)
# ---------------------------------------------------------------------------
VOLUME_SMA_PERIOD: int = 20          # trading days for 20-day volume SMA
ATR_PERIOD: int = 14
EMA_FAST_PERIOD: int = 20
EMA_SLOW_PERIOD: int = 50

# ---------------------------------------------------------------------------
# Swing sizing and exits
# ---------------------------------------------------------------------------
SIGNAL_SIZE_1_STRATEGY: float = 500.0
SIGNAL_SIZE_2_STRATEGIES: float = 750.0
SIGNAL_SIZE_3_PLUS_STRATEGIES: float = 1000.0
MAX_SWING_HOLD_DAYS: int = 14

# ---------------------------------------------------------------------------
# Legacy Track-A/Track-B constants retained for compatibility during migration
# ---------------------------------------------------------------------------
ALPACA_DATA_FEED: str = "sip"            # "iex" (free) | "sip" (paid)
SNAPSHOT_INTERVAL_SECONDS: float = 15.0
RVOL_MIN_THRESHOLD: float = 1.0          # minimum RVOL_20 for watchlist inclusion
HOT_WATCHLIST_SIZE: int = 50             # top-N symbols written to config.hot_watchlist
VOLUME_SURGE_MULTIPLIER: float = 3.0     # stronger signal threshold used by Track-B entry logic
GAP_PCT_MIN_THRESHOLD:  float = 2.5     # minimum gap-up % required for regular-hours universe filter

# ---------------------------------------------------------------------------
# Entry quality filters (Track-B)
# ---------------------------------------------------------------------------
MIN_VOLUME_AT_SIGNAL: int = 2000            # cumulative volume required before any entry fires
ENTRY_MAX_ABOVE_VWAP_SIGMA: float = 1.5     # entry price must be ≤ session VWAP + 1.5σ
GAP_PCT_MIN_PRICE_BELOW_3: float  = 15.0    # min gap% for stocks priced < $3
GAP_PCT_MIN_PRICE_BELOW_10: float = 8.0     # min gap% for stocks $3–$10
GAP_PCT_MIN_PRICE_ABOVE_10: float = 5.0     # min gap% for stocks ≥ $10

# ---------------------------------------------------------------------------
# Per-symbol re-entry cooldown
# ---------------------------------------------------------------------------
SYMBOL_COOLDOWN_LOSSES: int   = 2           # consecutive losses before cooldown activates
SYMBOL_COOLDOWN_SECS:   float = 1800.0      # 30-minute block after cooldown activates

# ---------------------------------------------------------------------------
# Track-B WebSocket feed
# ---------------------------------------------------------------------------
ALPACA_WS_URL: str = "wss://stream.data.alpaca.markets/v2/sip"

# ---------------------------------------------------------------------------
# Telegram alerts
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID:   str = os.getenv("TELEGRAM_CHAT_ID",   "")

# ---------------------------------------------------------------------------
# Pre-market news REST endpoint
# ---------------------------------------------------------------------------
ALPACA_NEWS_URL: str = f"{ALPACA_DATA_URL}/v1beta1/news"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
