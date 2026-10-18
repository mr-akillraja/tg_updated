# Algo Main TG

This project is a trading bot and validation harness. It keeps the existing EMA9 slope rule intact and adds governance, campaign tracking, and replay tooling without altering the browser login or order-click path.

Important: this repository is for strategy testing and validation. It does not promise profit or guarantee results.

## Run

```bash
conda activate tg_algo
cd /home/akill-sud/Documents/projects/algo_main_tg_updated\ \(1\)/algo_main_tg
python multi_agent_orchestrator.py --max-daily-trades 120 --max-daily-loss 20000
```

## Environment variables

Core runtime:
- OLYMPTRADE_EMAIL
- OLYMPTRADE_PASSWORD
- ASSET
- TRADE_AMOUNT
- TRADE_MODE
- FTT_DURATION
- ASSET_SCAN_INTERVAL_SECONDS
- CONFIDENCE_THRESHOLD
- MODEL_TYPE
- LOOKBACK_CANDLES
- CANDLE_TIMEFRAME
- MAX_DAILY_TRADES
- MAX_DAILY_LOSS
- MAX_DAILY_LOSS_USD
- STOP_LOSS_PCT
- POLL_INTERVAL_SECONDS
- STRATEGY_ENTRY_SECONDS
- LOG_DIR
- LOG_DATE_FORMAT
- LOG_ARCHIVE_DIR
- JOURNAL_ARCHIVE_DIR
- RESET_TODAY_LOGS_ON_STARTUP
- RL_USE_IN_LIVE_TRADING
- MODEL_DIR
- TARGET_PROFIT_PER_TRADE_USD
- MAX_TRADE_LOSS_USD
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
- TRAINING_MODE
- LOGIN_WAIT_SECONDS
- ALLOW_HEDGING
- CLOSE_OPPOSITE_ON_STRONG_SIGNAL
- DEMO_STRATEGY_ENABLED
- DEMO_ENTRY_TIMEFRAME_SECONDS
- DEMO_EMA_TOUCH_TOLERANCE_MULTIPLIER
- DEMO_ENTRY_DEADLINE_SECONDS

One-indicator strategy:
- ONE_INDICATOR_ENABLED
- ONE_INDICATOR_EMA_PERIOD
- ONE_INDICATOR_SLOPE_BARS
- ONE_INDICATOR_MIN_CANDLES
- ONE_INDICATOR_SLOPE_ATR_FRACTION
- ONE_INDICATOR_SLOPE_RANGE_FRACTION
- ONE_INDICATOR_PRICE_ATR_FRACTION
- ONE_INDICATOR_BODY_RATIO_MIN
- ONE_INDICATOR_OPPOSING_WICK_MAX_RATIO
- ONE_INDICATOR_TREND_ALIGN
- ONE_INDICATOR_TREND_STRONG_THRESHOLD
- ONE_INDICATOR_CHOP_FILTER
- ONE_INDICATOR_CROSSOVERS_WINDOW
- ONE_INDICATOR_MAX_CROSSOVERS
- ONE_INDICATOR_CHOP_BANDWIDTH_MAX
- ONE_INDICATOR_RSI_PERIOD
- ONE_INDICATOR_RSI_OVERBOUGHT
- ONE_INDICATOR_RSI_OVERSOLD
- ONE_INDICATOR_TICK_BIAS_CONFIRM
- ONE_INDICATOR_STALE_MAX_AGE_SECONDS
- ONE_INDICATOR_COOLDOWN_CANDLES
- ONE_INDICATOR_CONSECUTIVE_LOSS_HALT
- ONE_INDICATOR_TREND_NEUTRAL_ALLOWED
- ONE_INDICATOR_ENTRY_WINDOW_SECONDS
- ONE_INDICATOR_FILTER_STATS_EVERY
- ONE_INDICATOR_MIN_FILTERS_PASSED_MODE
- ONE_INDICATOR_SOFT_FILTERS_REQUIRED
- ONE_INDICATOR_MIN_TRADES_PER_HOUR
- ONE_INDICATOR_WARN_TRADES_PER_HOUR
- ONE_INDICATOR_BB_PERIOD
- ONE_INDICATOR_BB_STDDEV
- ONE_INDICATOR_RSI_BUY_THRESHOLD
- ONE_INDICATOR_RSI_SELL_THRESHOLD
- ONE_INDICATOR_VOL_BANDWIDTH_LOW
- ONE_INDICATOR_VOL_BANDWIDTH_HIGH

Governor controls:
- GOVERNOR_ENABLED
- GOVERNOR_MAX_PER_HOUR
- GOVERNOR_MIN_SPACING_SECONDS
- GOVERNOR_PERCENTILE
- GOVERNOR_WINDOW
- GOVERNOR_MIN_HISTORY
- GOVERNOR_MAX_PER_DAY
- GOVERNOR_STATE_PATH

Campaign controls:
- CAMPAIGN_TARGET_TRADES
- CAMPAIGN_SINCE
- CAMPAIGN_PAYOUT
- CAMPAIGN_EXTRA_JOURNAL_DIRS

Tick-tape / live feed:
- TICK_TAPE_ENABLED
- TICK_CONSOLE
- TICK_DEBUG
- FEED_RECOVERY
- FEED_RECOVERY_AFTER_SECONDS

Offline smoke validation:
- python tools/live_path_smoke.py
  - exercises the real one-indicator live decision path using synthetic ticks only
  - validates the [Pattern], [Governor], [OneIndicator], [Rate], and [Governor] state log lines without browser access

## [Rate] fields

The [Rate] log line reports the live governor/campaign pacing snapshot. Each field is:

- history: total seeded EMA strength samples retained for the governor history window.
- threshold: current percentile threshold used to block weak signals.
- taken_last_hour: accepted trade count in the last hour.
- taken_last_24h: accepted trade count in the last 24 hours.
- max_per_hour: configured hourly cap for accepted orders.
- max_per_day: configured daily cap for accepted orders.
- seeded_signals: same as history; how many EMA strength signals were seeded into the governor.
- seeded_top10pct: the top-10% slice of the seeded history, rounded to a count and capped to the available signal count.
- pattern_confirmed: pattern confirmation status for the active trade context; logs as n/a when unavailable.
- governor_selected: count of governor-selected entries currently tracked by the governor, derived from the taken timestamps list.
- orders_accepted_last_hour: same value as taken_last_hour for the rate snapshot.
- orders_accepted_last_24h: same value as taken_last_24h for the rate snapshot.
- projected_days_to_target: remaining target divided by accepted orders in the last 24h, or n/a when the 24h order count is zero. The live formula is round((target - decided) / orders_accepted_last_24h, 1) when orders_accepted_last_24h > 0; it stays n/a when the count is zero.

This is the live pacing metric used to estimate how many days remain before the campaign target is reached when the current 24h order rate continues.

## Campaign reset helper

The repository includes a safe reset script for campaign counting:

```bash
python tools/campaign_reset.py --start-now
python tools/campaign_reset.py --start 2026-10-16T12:00:00Z --yes
```

The script is intentionally dry-run by default. It only writes the campaign start marker when `--yes` is supplied, and it never runs automatically during startup.

## Verification

The baseline and final checks were executed with:

```bash
conda activate tg_algo
python -m pytest -q
```

Baseline before the startup/governor fixes: 476 passed, 5 warnings.
Final status after the additive fixes: 480 passed, 5 warnings.

## Notes

- The EMA9 rule remains intact and is not rewritten.
- Browser login, asset selection, and order-click flows remain unchanged.
- The governor and campaign features are additive and persist their state to JSON/CSV files under the logs folder.
