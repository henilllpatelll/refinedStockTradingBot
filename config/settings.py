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
MAX_POSITION_COST: float = 500.0

# Maximum dollar loss tolerated on any single trade before hard exit (USD)
MAX_RISK_PER_TRADE: float = 40.0

# ---------------------------------------------------------------------------
# Universe sweep parameters (Tier-1, 3:30 AM)
# ---------------------------------------------------------------------------
FINVIZ_SCREENER_BASE_URL: str = (
    "https://finviz.com/screener.ashx"
    "?v=111&f=sh_float_u20&ft=4&o=-volume"
)
MAX_UNIVERSE_SIZE: int = 500         # cap symbols carried into Tier-2; Alpaca batch snapshot handles up to 1000

# ---------------------------------------------------------------------------
# Alpha Vantage (Tier-1 fallback)
# ---------------------------------------------------------------------------
ALPHA_VANTAGE_API_KEY: str = os.getenv("ALPHA_VANTAGE_API_KEY", "demo")
ALPHA_VANTAGE_BASE_URL: str = "https://www.alphavantage.co/query"

# ---------------------------------------------------------------------------
# Baseline calculation parameters (Tier-2, 3:45 AM)
# ---------------------------------------------------------------------------
VOLUME_SMA_PERIOD: int = 20          # trading days for 20-day volume SMA

# ---------------------------------------------------------------------------
# Track-A REST snapshot loop
# ---------------------------------------------------------------------------
ALPACA_DATA_FEED: str = "sip"            # "iex" (free) | "sip" (paid)
SNAPSHOT_INTERVAL_SECONDS: float = 15.0
RVOL_MIN_THRESHOLD: float = 2.0          # minimum RVOL_20 for watchlist inclusion
HOT_WATCHLIST_SIZE: int = 10             # top-N symbols written to config.hot_watchlist
VOLUME_SURGE_MULTIPLIER: float = 3.0     # stronger signal threshold used by Track-B entry logic
GAP_PCT_MIN_THRESHOLD:  float = 5.0     # minimum gap-up % required for regular-hours universe filter

# ---------------------------------------------------------------------------
# Track-B WebSocket feed
# ---------------------------------------------------------------------------
ALPACA_WS_URL: str = "wss://stream.data.alpaca.markets/v2/sip"

# ---------------------------------------------------------------------------
# News Analyst LLM (agents/news_analyst.py)
# ---------------------------------------------------------------------------
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL:   str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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
