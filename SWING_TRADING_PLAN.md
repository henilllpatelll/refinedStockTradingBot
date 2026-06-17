# Swing Trading Bot Rebuild Plan

## Context

The current bot is an intraday low-float momentum day trader: it scouts RVOL spikes in real-time via WebSocket ticks, enters ORB+CVD breakouts at market open, and force-closes all positions by 3:55 PM. This is a complete rebuild as a **multi-strategy swing trading bot** that holds positions 2 days to 2 weeks, tracks 7 parallel strategies with per-strategy performance data, and never force-closes on the day. Long-only. Paper trading on Alpaca.

---

## New Bot Architecture Overview

```
SATURDAY 8:00 AM ET
  tier1_universe_sweep.py   ← Finviz swing filters + earnings calendar + sector rankings

SUNDAY 6:00 PM ET
  watchlist_pruner.py       ← Remove broken setups from swing_watchlist.json

WEEKDAY 3:45 PM ET
  tier2_baseline_calc.py    ← Daily bars → ATR, EMA20/50, 52wk high
  eod_scanner.py            ← Run 7 strategies → build next-day watchlist

WEEKDAY 7:00 AM ET
  premarket_filter.py       ← Overnight news/catalyst + gap check → confirm or kill setups

WEEKDAY 9:30 AM ET
  swing_entry.py            ← Place limit orders for confirmed setups

CONTINUOUS (market hours)
  position_manager.py       ← Intraday emergency checks, trailing stops, fill handling

WEEKDAY 3:50 PM ET
  eod_close_check           ← Daily candle exit signals (no force-close)

WEEKDAY 4:05 PM ET
  strategy_analytics.py     ← Per-strategy stats → Telegram daily summary
```

---

## Universe & Stock Filters
- Min price: **$2**
- Min avg daily volume: **500k shares/day**
- No float restriction, no market cap ceiling (mixed universe)
- Blocked: secondary offerings, bankruptcy, delisting keywords

---

## 7 Strategies (run in parallel on all stocks)

| ID | Name | Key Entry Conditions | Hold Time |
|---|---|---|---|
| S1 | Breakout | Close > prior swing high; RVOL ≥ 1.5×; catalyst within 3 days | 2–5 days |
| S2 | 52-Week High | New 52wk high; volume > 1.5× SMA20; price > EMA50 | 2–5 days |
| S4 | Earnings Momentum | Earnings beat last 2 days; gap up ≥ 3%; holding above earnings-day low | 3–7 days |
| S5 | Pullback to EMA20 | Uptrend (price > EMA50); low within 2% of EMA20; volume declining 3 days | 3–8 days |
| S9 | Flag/Pennant | Impulse ≥ 5% in 1–3 days; 3–7 day consolidation < 3% range; breakout on 2×+ vol | 4–10 days |
| S12 | Sector Rotation | Sector in top-3 weekly RS; stock RS > sector ETF; price > EMA20 | 5–14 days |
| S13 | Analyst Upgrade | Upgrade/PT raise last 2 days; price > prior close; volume ≥ 1.2× SMA20 | 3–7 days |

**Conflict rule:** One position per strategy per stock (same stock can have up to 7 positions if all strategies fire).

---

## Exit Rules Per Strategy

| Strategy | T1 Target | Trail Stop | Split |
|---|---|---|---|
| S1 Breakout | +3% | 2.5% | 50/50 |
| S2 52wk High | +3% | 2.5% | 50/50 |
| S4 Earnings Mom | +6% | 4.0% | 50/50 |
| S5 Pullback EMA20 | +4% | 3.0% | 50/50 |
| S9 Flag/Pennant | +4% | 3.0% | 50/50 |
| S12 Sector Rotation | +5% | 3.5% | 50/50 |
| S13 Analyst Upgrade | +6% | 4.0% | 50/50 |

**All strategies:**
- Stop loss: 1.5× ATR(14) below entry
- Break-even stop activates after T1 fills
- Emergency exit: floating loss ≤ -$40 OR daily close < EMA20
- Hold through weekends (no Friday force-close)

---

## Position Sizing (by signal strength)
- 1 strategy fires on a stock → **$500**
- 2 strategies fire → **$750**
- 3+ strategies fire → **$1,000**

---

## Catalyst Types (tracked per trade)
| Type | Triggered By |
|---|---|
| `earnings_beat` | EPS beat + guidance raise |
| `analyst_upgrade` | Upgrade or price target raise |
| `sector_tailwind` | Sector rotating into top-3 |
| `technical_breakout` | Price/volume pattern only |
| `insider_buy` | SEC Form 4 large purchase |
| `fda_approval` | Drug/device approval |
| `contract_win` | Government contract, partnership |
| `macro_positive` | Fed cut, strong GDP, etc. |

**Block signals** (symbol added to `blocked_tickers`): `secondary_offering`, `bankruptcy`, `delisted`

---

## Macro News Monitored
- Fed decisions, rate changes, FOMC minutes
- CPI, PPI, PCE inflation data
- NFP, unemployment rate
- GDP, PMI data
- Geopolitical events (war, sanctions, tariffs)
- Sector regulatory (FDA pipeline, AI legislation, energy policy)

---

## Analytics & Reporting
- **Per-trade JSON log** (`logs/trades_log.json`): symbol, strategy_id, catalyst_type, entry/exit price, P&L, hold_days, exit_reason, signal_strength
- **Telegram entry alert**: includes strategy tag + catalyst type
- **Telegram exit alert**: includes P&L breakdown + strategy tag
- **Telegram daily summary** (4:05 PM): per-strategy win rate, total P&L, best/worst trade
- **On-demand scoreboard**: `python -m execution.strategy_analytics` prints ranked strategy table

---

## Data Sources
- **Finviz**: Universe sweep, sector data, analyst upgrade fields
- **Alpaca**: Historical daily bars, news feed, order execution, corporate actions
- **Sector ETFs**: XLK, XLF, XLE, XLV, XLY, XLI, XLB, XLU, XLRE, XLC (weekly RS ranking)
- **Alpaca news WS**: Real-time keyword parsing for upgrades, earnings, catalysts, blocks
- **Alpha Vantage**: Fallback for universe if Finviz unavailable

---

## File-by-File Changes

### REPLACE
| File | Replacement | Reason |
|---|---|---|
| `strategies/tier1_universe_sweep.py` | Same file, new filters | Swing universe (no float cap, min $2, 500k vol); add earnings calendar + sector ranking fetch |
| `strategies/trackA_volume_scout.py` | `strategies/eod_scanner.py` + `strategies/premarket_filter.py` | EOD daily-bar scan replaces intraday RVOL WebSocket scout |
| `execution/trackB_realtime.py` | `execution/swing_entry.py` | Daily limit orders at open replace tick-by-tick CVD entry engine |
| `agents/news_analyst.py` | Same file, full rewrite | Add sentiment, catalyst typing, macro detection, source ranking, block signals |
| `main.py` | Same file, new schedule | Weekend tasks + EOD scan + no force-close |
| `config/settings.py` | Same file, new constants | Swing parameters replace intraday constants |

### MODIFY
| File | Changes |
|---|---|
| `strategies/tier2_baseline_calc.py` | Add ATR(14), EMA20/50 daily, 52wk high to baseline output; move run time to 4:05 PM |
| `execution/position_manager.py` | Add strategy_id/catalyst_type to _PositionState; per-strategy exit params; ATR stop; remove EOD force-close; add daily close check; remove L2/T&S tick pressure |
| `agents/premarket_news.py` | Simplify to 7 AM REST poll; keep live WS for blocking open positions |
| `agents/telegram_notifier.py` | Add strategy_id + catalyst_type to alert messages |
| `config/__init__.py` | Replace intraday shared state with swing state |
| `config/rejection_tracker.py` | Update stages for swing pipeline |
| `tests/test_indicators.py` | Remove CVD/L2 tests; add ATR, EMA daily, stop price tests |
| `tests/test_news_catalyst.py` | Add catalyst type tagging, block signal, macro detection tests |

### NEW
| File | Purpose |
|---|---|
| `strategies/eod_scanner.py` | 3:45 PM: evaluates universe against 7 strategies using daily bars |
| `strategies/premarket_filter.py` | 7:00 AM: news + gap filter on EOD watchlist |
| `strategies/watchlist_pruner.py` | Sunday 6 PM: remove broken setups |
| `strategies/playbook/__init__.py` | Package init |
| `strategies/playbook/s1_breakout.py` | S1 signal logic |
| `strategies/playbook/s2_52wk_high.py` | S2 signal logic |
| `strategies/playbook/s4_earnings_momentum.py` | S4 signal logic |
| `strategies/playbook/s5_pullback_ema20.py` | S5 signal logic |
| `strategies/playbook/s9_flag_pennant.py` | S9 signal logic |
| `strategies/playbook/s12_sector_rotation.py` | S12 signal logic |
| `strategies/playbook/s13_analyst_upgrade.py` | S13 signal logic |
| `agents/earnings_calendar.py` | Saturday earnings fetch |
| `agents/sector_ranker.py` | Sector weekly RS ranking |
| `execution/swing_entry.py` | 9:30 AM limit order placement |
| `execution/trade_logger.py` | Append per-trade JSON record |
| `execution/strategy_analytics.py` | Per-strategy metrics + Telegram daily summary |
| `tests/test_strategies.py` | Unit tests for all 7 strategy check_signal() functions |

### DELETE
| File | Reason |
|---|---|
| `strategies/trackA_volume_scout.py` | Replaced by eod_scanner + premarket_filter |
| `execution/trackB_realtime.py` | Replaced by swing_entry |

### KEEP AS-IS
- `config/market_hours.py`
- `execution/account_state.py`

---

## Reused Patterns & Functions
- `parse_finviz_number()` — tier1_universe_sweep.py (reuse everywhere Finviz data is parsed)
- Async Semaphore-gated batch fetch — tier2_baseline_calc.py
- `_ema_step()` — trackB_realtime.py → move to a shared `utils/indicators.py`
- Split-bracket exit structure — position_manager.py (keep, parameterize per strategy)
- Watchdog pattern (2s stall → 1.5× replace) — position_manager.py (keep as-is)
- `_wait_until()`, `_is_stale()` — main.py (keep as-is)
- Wake-lock daemon — main.py (keep as-is)
- asyncio.Lock shared state pattern — config/__init__.py

---

## Verification Checklist
1. `python main.py` weekday → EOD scan completes, `swing_watchlist.json` populated with strategy tags
2. `python main.py` Saturday → `earnings_calendar.json` and `sector_rankings.json` written
3. Manual trigger of `swing_entry.py` → limit orders appear on Alpaca paper account dashboard
4. Simulate fill via Alpaca sandbox → Telegram entry alert fires with strategy + catalyst tag
5. Simulate T1 fill → Telegram exit alert fires, `logs/trades_log.json` entry appended correctly
6. `python -m execution.strategy_analytics` → per-strategy scoreboard prints
7. Telegram daily summary fires at 4:05 PM with all 7 strategies listed
8. `pytest tests/` → all tests pass including 7 × 3 new strategy unit tests
