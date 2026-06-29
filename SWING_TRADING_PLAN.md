# Institutional Swing Trading Routine

This bot now runs one strategy only: `ISR`, the institutional swing routine.

The daily question is:

Which stocks are institutions accumulating that can move over the next 2 days to 4 weeks?

The bot looks for the alignment described in the routine:

- Strong theme or catalyst
- Strong weekly and daily chart
- Strong relative strength, sector leadership, and volume

## Chart Workflow

### Weekly Chart

Used to decide whether the stock is worth trading.

- Price above the 10-week moving average
- Price above the 40-week moving average
- 10-week moving average above the 40-week moving average
- Higher highs and higher lows
- Near highs rather than badly broken
- Outperforming the market or its sector

### Daily Chart

Used to decide whether there is a swing setup.

- Price above the 20-day EMA
- Price above the 50-day SMA
- Pullback to support
- Tight consolidation breakout
- Post-earnings gap-and-hold
- New-high breakout on volume
- No heavy-volume breakdown

### 65-Minute Chart

Used only for entry timing.

- VWAP reclaim or VWAP hold
- Higher lows or tight consolidation
- Breakout over the 65-minute range or setup trigger
- Strong close in the upper part of the bar

The bot does not use 1-minute or 5-minute charts for swing entries.

## Valid Setup Types

- Pullback reversal
- Tight consolidation breakout
- Earnings gap-and-hold
- High-volume breakout to new highs

## Avoid Rules

The bot skips entries when:

- The market regime is risk-off
- The stock is extended far above the 20-day EMA
- The stock fails VWAP on the 65-minute entry bar
- The stock breaks support before entry
- Relative strength or sector confirmation is missing
- The setup cannot define a stop

## Schedule

- Sunday: prune and prepare the watchlist
- 7:00 AM ET: pre-market news and gap filter
- 10:35 AM ET: first 65-minute confirmation after the open
- 3:00 PM ET and 3:30 PM ET: power-hour entry checks
- 3:45 PM ET: baseline and EOD scan
- 3:50 PM ET: daily close exit checks
- 4:05 PM ET: daily strategy summary

## Risk And Exits

- Strategy ID: `ISR`
- Target hold: 2 days to 4 weeks
- Max hold: 20 trading days
- Stop: 1.5 ATR below entry, with key support tracked when available
- Partial profit: 10%
- Runner trail: 7%
- Daily close exit: close below 20-day EMA or key support
