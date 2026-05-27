import os


def pytest_configure(config):
    """Set required env vars before any module imports during test collection."""
    os.environ.setdefault("ALPACA_PAPER_API_KEY", "test_key_id")
    os.environ.setdefault("ALPACA_PAPER_SECRET_KEY", "test_secret_key")
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
    os.environ.setdefault("TELEGRAM_CHAT_ID", "")
