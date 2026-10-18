"""config/settings.py — v3"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env', override=True)

EMAIL    = os.getenv("OLYMPTRADE_EMAIL", "")
PASSWORD = os.getenv("OLYMPTRADE_PASSWORD", "")
ASSET    = os.getenv("ASSET", "EUR/USD OTC")
TRADE_AMOUNT  = float(os.getenv("TRADE_AMOUNT", "1000"))
TRADE_MODE    = os.getenv("TRADE_MODE", "ftt").lower()
FTT_DURATION  = int(os.getenv("FTT_DURATION", "60"))
ASSET_SCAN_INTERVAL = int(os.getenv("ASSET_SCAN_INTERVAL_SECONDS", "99999"))

# Confidence gate — raise to 0.65 once profitable
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.50"))
MODEL_TYPE           = os.getenv("MODEL_TYPE", "ensemble")
LOOKBACK_CANDLES     = int(os.getenv("LOOKBACK_CANDLES", "200"))
CANDLE_TIMEFRAME     = os.getenv("CANDLE_TIMEFRAME", "M1")

MAX_DAILY_TRADES   = int(os.getenv("MAX_DAILY_TRADES", "50"))
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", os.getenv("MAX_DAILY_LOSS_USD", "10")))
MAX_DAILY_LOSS_USD = MAX_DAILY_LOSS
STOP_LOSS_PCT      = float(os.getenv("STOP_LOSS_PCT", "0.02"))
# Monitoring cadence is independent of candle periods and broker expiry.
POLL_INTERVAL      = float(os.getenv("POLL_INTERVAL_SECONDS", "1"))
ACTIVE_CANDLE_SECONDS = (30, 60)
# Apply the existing entry rules to completed 30s candles; 60 retains legacy entries.
STRATEGY_ENTRY_SECONDS = int(os.getenv("STRATEGY_ENTRY_SECONDS", "30"))
TREND_FILTER_SECONDS = 300
QUOTE_MAX_AGE_SECONDS = 2.0
HEADLESS           = os.getenv("HEADLESS", "false").lower() in ("1","true","yes")
LOG_DIR            = os.getenv("LOG_DIR",   "logs")
LOG_DATE_FORMAT            = os.getenv("LOG_DATE_FORMAT", "%Y%m%d")
LOG_ARCHIVE_DIR            = os.getenv("LOG_ARCHIVE_DIR", os.path.join(LOG_DIR, "archive"))
JOURNAL_ARCHIVE_DIR        = os.getenv("JOURNAL_ARCHIVE_DIR", os.path.join(LOG_DIR, "archive"))
RESET_TODAY_LOGS_ON_STARTUP = os.getenv("RESET_TODAY_LOGS_ON_STARTUP", "false").lower() in ("1", "true", "yes")
RL_USE_IN_LIVE_TRADING     = os.getenv("RL_USE_IN_LIVE_TRADING", "false").lower() in ("1", "true", "yes")
MODEL_DIR                  = os.getenv("MODEL_DIR", "models")
TARGET_PROFIT_PER_TRADE_USD = float(os.getenv("TARGET_PROFIT_PER_TRADE_USD", "100.0"))
MAX_TRADE_LOSS_USD = float(os.getenv("MAX_TRADE_LOSS_USD", "500.0"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
TRAINING_MODE = os.getenv("TRAINING_MODE", "false").lower() in ("1", "true", "yes")
LOGIN_WAIT_SECONDS = int(os.getenv("LOGIN_WAIT_SECONDS", "300"))

ALLOW_HEDGING = os.getenv("ALLOW_HEDGING", "true").lower() in ("1","true","yes")
CLOSE_OPPOSITE_ON_STRONG_SIGNAL = os.getenv("CLOSE_OPPOSITE_ON_STRONG_SIGNAL", "false").lower() in ("1","true","yes")

# Strategy selection; keep the original gate as the default and switch to the demo
# continuation model only when explicitly enabled.
ACTIVE_STRATEGY = os.getenv("ACTIVE_STRATEGY", "ema_pattern").strip().lower()
DEMO_STRATEGY_ENABLED = os.getenv("DEMO_STRATEGY_ENABLED", "false").lower() in ("1", "true", "yes")
DEMO_ENTRY_TIMEFRAME_SECONDS = int(os.getenv("DEMO_ENTRY_TIMEFRAME_SECONDS", "30"))
DEMO_EMA_TOUCH_TOLERANCE_MULTIPLIER = float(os.getenv("DEMO_EMA_TOUCH_TOLERANCE_MULTIPLIER", "0.15"))
DEMO_ENTRY_DEADLINE_SECONDS = float(os.getenv("DEMO_ENTRY_DEADLINE_SECONDS", "5.0"))


# ── One-indicator strategy + tick-driven live feed (additive) ────────────────
# Keep the existing EMA9 slope signal as the core entry trigger and tighten it
# with optional confirmation filters. This preserves the legacy interface and
# leaves browser automation and risk logic untouched.
ONE_INDICATOR_ENABLED = os.getenv("ONE_INDICATOR_ENABLED", "true").lower() in ("1", "true", "yes")
ONE_INDICATOR_EMA_PERIOD = int(os.getenv("ONE_INDICATOR_EMA_PERIOD", "9"))
ONE_INDICATOR_SLOPE_BARS = int(os.getenv("ONE_INDICATOR_SLOPE_BARS", "2"))
ONE_INDICATOR_MIN_CANDLES = int(os.getenv("ONE_INDICATOR_MIN_CANDLES", "30"))
ONE_INDICATOR_SLOPE_ATR_FRACTION = float(os.getenv("ONE_INDICATOR_SLOPE_ATR_FRACTION", "0.15"))
ONE_INDICATOR_SLOPE_RANGE_FRACTION = float(os.getenv("ONE_INDICATOR_SLOPE_RANGE_FRACTION", "0.12"))
ONE_INDICATOR_PRICE_ATR_FRACTION = float(os.getenv("ONE_INDICATOR_PRICE_ATR_FRACTION", "0.25"))
ONE_INDICATOR_BODY_RATIO_MIN = float(os.getenv("ONE_INDICATOR_BODY_RATIO_MIN", "0.40"))
ONE_INDICATOR_OPPOSING_WICK_MAX_RATIO = float(os.getenv("ONE_INDICATOR_OPPOSING_WICK_MAX_RATIO", "0.35"))
ONE_INDICATOR_TREND_ALIGN = os.getenv("ONE_INDICATOR_TREND_ALIGN", "true").lower() in ("1", "true", "yes")
ONE_INDICATOR_TREND_STRONG_THRESHOLD = float(os.getenv("ONE_INDICATOR_TREND_STRONG_THRESHOLD", "0.015"))
ONE_INDICATOR_CHOP_FILTER = os.getenv("ONE_INDICATOR_CHOP_FILTER", "true").lower() in ("1", "true", "yes")
ONE_INDICATOR_CROSSOVERS_WINDOW = int(os.getenv("ONE_INDICATOR_CROSSOVERS_WINDOW", "12"))
ONE_INDICATOR_MAX_CROSSOVERS = int(os.getenv("ONE_INDICATOR_MAX_CROSSOVERS", "3"))
ONE_INDICATOR_CHOP_BANDWIDTH_MAX = float(os.getenv("ONE_INDICATOR_CHOP_BANDWIDTH_MAX", "0.08"))
ONE_INDICATOR_RSI_PERIOD = int(os.getenv("ONE_INDICATOR_RSI_PERIOD", "14"))
ONE_INDICATOR_RSI_OVERBOUGHT = int(os.getenv("ONE_INDICATOR_RSI_OVERBOUGHT", "70"))
ONE_INDICATOR_RSI_OVERSOLD = int(os.getenv("ONE_INDICATOR_RSI_OVERSOLD", "30"))
ONE_INDICATOR_TICK_BIAS_CONFIRM = os.getenv("ONE_INDICATOR_TICK_BIAS_CONFIRM", "true").lower() in ("1", "true", "yes")
ONE_INDICATOR_STALE_MAX_AGE_SECONDS = float(os.getenv("ONE_INDICATOR_STALE_MAX_AGE_SECONDS", "2.0"))
ONE_INDICATOR_COOLDOWN_CANDLES = int(os.getenv("ONE_INDICATOR_COOLDOWN_CANDLES", "3"))
ONE_INDICATOR_CONSECUTIVE_LOSS_HALT = int(os.getenv("ONE_INDICATOR_CONSECUTIVE_LOSS_HALT", "3"))
ONE_INDICATOR_TREND_NEUTRAL_ALLOWED = os.getenv("ONE_INDICATOR_TREND_NEUTRAL_ALLOWED", "true").lower() in ("1", "true", "yes")
ONE_INDICATOR_ENTRY_WINDOW_SECONDS = float(os.getenv("ONE_INDICATOR_ENTRY_WINDOW_SECONDS", "3.0"))
ONE_INDICATOR_FILTER_STATS_EVERY = int(os.getenv("ONE_INDICATOR_FILTER_STATS_EVERY", "50"))
ONE_INDICATOR_MIN_FILTERS_PASSED_MODE = os.getenv("ONE_INDICATOR_MIN_FILTERS_PASSED_MODE", "false").lower() in ("1", "true", "yes")
ONE_INDICATOR_SOFT_FILTERS_REQUIRED = int(os.getenv("ONE_INDICATOR_SOFT_FILTERS_REQUIRED", "3"))
ONE_INDICATOR_MIN_TRADES_PER_HOUR = float(os.getenv("ONE_INDICATOR_MIN_TRADES_PER_HOUR", "3.0"))
ONE_INDICATOR_WARN_TRADES_PER_HOUR = float(os.getenv("ONE_INDICATOR_WARN_TRADES_PER_HOUR", "1.0"))

GOVERNOR_ENABLED = os.getenv("GOVERNOR_ENABLED", "true").lower() in ("1", "true", "yes")
GOVERNOR_MAX_PER_HOUR = int(os.getenv("GOVERNOR_MAX_PER_HOUR", "5"))
GOVERNOR_MIN_SPACING_SECONDS = float(os.getenv("GOVERNOR_MIN_SPACING_SECONDS", "480"))
GOVERNOR_PERCENTILE = float(os.getenv("GOVERNOR_PERCENTILE", "90"))
GOVERNOR_WINDOW = int(os.getenv("GOVERNOR_WINDOW", "300"))
GOVERNOR_MIN_HISTORY = int(os.getenv("GOVERNOR_MIN_HISTORY", "40"))
GOVERNOR_MAX_PER_DAY = int(os.getenv("GOVERNOR_MAX_PER_DAY", "110"))
GOVERNOR_STATE_PATH = os.getenv("GOVERNOR_STATE_PATH", "logs/governor_state.json")

CAMPAIGN_TARGET_TRADES = int(os.getenv("CAMPAIGN_TARGET_TRADES", "1000"))
CAMPAIGN_SINCE = os.getenv("CAMPAIGN_SINCE", "")
CAMPAIGN_PAYOUT = float(os.getenv("CAMPAIGN_PAYOUT", "0.85"))
CAMPAIGN_EXTRA_JOURNAL_DIRS = os.getenv("CAMPAIGN_EXTRA_JOURNAL_DIRS", "")

# Back-compat aliases for older startup code that still references the legacy
# confluence-style field names. These are kept as defaults only and do not alter
# the active EMA9 strategy behavior.
ONE_INDICATOR_BB_PERIOD = int(os.getenv("ONE_INDICATOR_BB_PERIOD", "20"))
ONE_INDICATOR_BB_STDDEV = float(os.getenv("ONE_INDICATOR_BB_STDDEV", "2.0"))
ONE_INDICATOR_RSI_BUY_THRESHOLD = int(os.getenv("ONE_INDICATOR_RSI_BUY_THRESHOLD", str(ONE_INDICATOR_RSI_OVERSOLD)))
ONE_INDICATOR_RSI_SELL_THRESHOLD = int(os.getenv("ONE_INDICATOR_RSI_SELL_THRESHOLD", str(ONE_INDICATOR_RSI_OVERBOUGHT)))
ONE_INDICATOR_VOL_BANDWIDTH_LOW = float(os.getenv("ONE_INDICATOR_VOL_BANDWIDTH_LOW", "0.0015"))
ONE_INDICATOR_VOL_BANDWIDTH_HIGH = float(os.getenv("ONE_INDICATOR_VOL_BANDWIDTH_HIGH", "0.30"))

TICK_TAPE_ENABLED = os.getenv("TICK_TAPE_ENABLED", "true").lower() in ("1", "true", "yes")
TICK_CONSOLE = os.getenv("TICK_CONSOLE", "false").lower() in ("1", "true", "yes")
