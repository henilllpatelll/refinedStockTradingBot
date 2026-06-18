from __future__ import annotations

import logging

from utils.market_data import fetch_daily_bars

_log = logging.getLogger(__name__)
_REGIME_SYMBOLS = ["SPY", "QQQ"]
_LOOKBACK_DAYS = 320   # calendar days → ~220 trading days, enough for SMA200 + buffer
_BAR_LIMIT = 225


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _distribution_days(df, window: int = 25) -> int:
    """Count days in the last `window` bars where price fell ≥0.2% on above-average volume.

    Volume threshold is the 50-day average computed from bars *before* the measurement
    window, so we don't inflate the baseline with the very days we're counting.
    """
    closes = list(df["close"])
    volumes = list(df["volume"])
    if len(closes) < window + 2:
        return 0
    ref_vols = volumes[: len(volumes) - window]
    avg_vol = sum(ref_vols[-50:]) / min(50, len(ref_vols)) if ref_vols else None
    if not avg_vol:
        return 0
    count = 0
    start = len(closes) - window
    for i in range(start, len(closes)):
        change = (closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] > 0 else 0.0
        if change <= -0.002 and volumes[i] > avg_vol:
            count += 1
    return count


def _symbol_regime(df) -> str:
    """Classify one index as UPTREND / NEUTRAL / DOWNTREND.

    Rules (applied in priority order):
      DOWNTREND : close < SMA200, or ≥5 distribution days in last 25 sessions
      NEUTRAL   : close < SMA50 (but above SMA200 and <5 dist days)
      UPTREND   : close > SMA50, close > SMA200, <4 distribution days
    """
    closes = list(df["close"])
    if not closes:
        return "NEUTRAL"
    close = closes[-1]
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    dist = _distribution_days(df)
    if sma200 is not None and close < sma200:
        return "DOWNTREND"
    if dist >= 5:
        return "DOWNTREND"
    if sma50 is not None and close < sma50:
        return "NEUTRAL"
    if sma50 is not None and close > sma50 and (sma200 is None or close > sma200) and dist < 4:
        return "UPTREND"
    return "NEUTRAL"


async def get_market_regime() -> str:
    """Return overall market regime: UPTREND, NEUTRAL, or DOWNTREND.

    Either index in DOWNTREND → DOWNTREND.
    Both indexes in UPTREND → UPTREND.
    Otherwise → NEUTRAL.
    """
    try:
        bars = await fetch_daily_bars(
            _REGIME_SYMBOLS, lookback_days=_LOOKBACK_DAYS, limit=_BAR_LIMIT
        )
    except Exception as exc:
        _log.warning("MarketRegime | data fetch failed, defaulting NEUTRAL: %s", exc)
        return "NEUTRAL"
    regimes: list[str] = []
    for sym in _REGIME_SYMBOLS:
        df = bars.get(sym)
        if df is None or df.empty:
            _log.warning("MarketRegime | no data for %s — skipping", sym)
            continue
        regime = _symbol_regime(df)
        _log.info("MarketRegime | %s → %s (close=%.2f)", sym, regime, df["close"].iloc[-1])
        regimes.append(regime)
    if not regimes:
        _log.warning("MarketRegime | no index data available — defaulting NEUTRAL")
        return "NEUTRAL"
    if "DOWNTREND" in regimes:
        return "DOWNTREND"
    if all(r == "UPTREND" for r in regimes):
        return "UPTREND"
    return "NEUTRAL"
