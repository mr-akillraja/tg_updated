# BUY and SELL demo monitoring

The configured EUR/USD instrument is evaluated every **1 second**. Entry checks
now use a **completed 30-second candle** by default, so shorter candles directly
participate in entry decisions. `--entry-seconds 60` restores the previous
one-minute entry period. The environment default is `STRATEGY_ENTRY_SECONDS=30`;
an explicit `--entry-seconds {30,60}` overrides it. Broker trade duration remains
**60 seconds from entry**, independent of either candle boundary or polling time.

Both existing BUY and SELL rules can authorize orders. The strategy rules and
numeric thresholds in `strategies/closed_candle.py` remain the same. The
30-second trend check still uses
EMA20/50. Entry checks use EMA9/21 and the existing RSI14 bands on the selected
entry period. When entry is 30 seconds, the same completed candle series supplies
both roles, each with its own indicators and requirements. EMA, RSI, candle
geometry and swing calculations consequently describe shorter price movements.
Live EMA, RSI, candle geometry, support/resistance and HH/HL/LH/LL diagnostics
are provisional. Confirmed swings still need two completed candles on each
side, and entry conditions requiring a close still use completed candles.
The five-minute filter must agree with the proposed trade. BUY requires price
above EMA21, EMA9 above EMA21, and both EMAs rising over three completed bars.
SELL requires price below EMA21, EMA9 below EMA21, and both EMAs falling over
three completed bars. It needs 24 contiguous completed five-minute candles.
Neutral or opposite-direction conditions block entry. The filter's periods and
slope requirements are unchanged by the entry-period setting.

The existing entry rules use confirmed swings and breakouts to identify the
pattern; visible peaks or troughs alone do not authorize orders:

| Requirement | BUY / Up | SELL / Down |
| --- | --- | --- |
| 30-second trend | Close above EMA20; EMA20 above EMA50; both rising | Close below EMA20; EMA20 below EMA50; both falling |
| Entry EMA alignment | EMA9 above EMA21; close above both | EMA9 below EMA21; close below both |
| Last five entry closes | At or above EMA21 | At or below EMA21 |
| Pullback progression | Confirmed higher low; entry low above the prior swing low | Confirmed lower high; entry high below the prior swing high |
| Breakout close | Above the latest confirmed swing high and previous candle high | Below the latest confirmed swing low and previous candle low |
| RSI14 | 50–65 inclusive and rising | 35–50 inclusive and falling |
| Obstacle clearance | Resistance more than 1.5 confirmation-candle ranges away, or none identified | Support more than 1 confirmation-candle range away, or none identified |

Both directions reject confirmation candles exceeding twice the average range
of the previous ten candles. The existing SELL rules retain their fallback of
three descending candle highs when confirmed pullback swings are insufficient,
and three descending lows when no confirmed breakout swing is available.
The BUY rules still require their confirmed swings. These asymmetries and all
numeric thresholds are preserved.

Each `[Decision]` log record contains BUY, SELL, WAIT or NO TRADE with a reason,
forming snapshots and their remaining time. The latest record is also
available as `system.latest_decision`. The terminal shows a compact summary;
log files retain full JSON. `buy_evaluation` and `sell_evaluation` describe each
direction's closed-candle checks and are confirmed only when `passed` is true.
`direction` identifies the selected confirmed BUY/SELL setup, or is null when
neither side qualifies. `entry_evaluation` contains the selected side's checks;
when neither side qualifies, it contains the side chosen for diagnostic context.
`diagnostic_direction` names that side: a selected setup takes precedence;
otherwise EMA20 below EMA50 selects SELL diagnostics and other conditions use BUY.
`trend_filter_5m.required_direction` identifies the requested BUY/SELL direction,
while its `direction` reports the physical bullish/bearish/neutral trend.
`trend_close` is the completed
30-second close; `entry_close` is the completed close for the selected entry
period. `entry_timeframe_seconds` appears in the record and each evaluation.
The separate `entry_candle` field carries live diagnostics for the selected entry
period, including EMA9/21; `candles` retains the 30-second trend and one-minute
diagnostics. Live RSI includes the unfinished candle and is labeled separately
from closed RSI.
Required RSI bands and resistance/support clearance accompany observed values.
`blocking_stage` identifies data, entry rules, the 5m filter, timing, risk or
execution. `accepted_orders_this_run` counts broker acceptances, not signals.
A submission is followed by an explicit accepted, rejected or uncertain result;
rejected submissions cannot leave the latest status showing BUY or SELL.
The terminal shows the first blocking reason and failed-rule count; complete
failure lists remain in the file JSON and candle-close gate records. Quote age,
quote freshness, stale candle timeframes and entry-window status distinguish
data delays from rejected strategy setups.
`[Seed]` distinguishes received history from the smaller retained candle buffers
and reports the number of aggregated five-minute candles separately. A retained
minute count of 70 does not indicate insufficient warmup when enough five-minute
candles were built from the full received history.

`--chart-view entry` starts the selected entry period with EMA9/21,
`--chart-view trend` starts 30 seconds with EMA20/50, and `--chart-view filter5m`
starts five minutes with EMA9/21. Each view shows RSI14; trend/filter RSI is
diagnostic, while the selected entry candle's RSI participates in entry checks.
The updater skips busy controls, unsupported intervals and open trades. It starts
only between offsets 10 and `min(40, entry_seconds - 14)` within the entry cycle:
seconds 10–16 of each 30-second cycle, or seconds 10–40 of each one-minute cycle.
An eight-second update timeout plus bounded cleanup protects the five-second
entry window. All strategy timeframes are monitored regardless of the display.

Native history refresh and order execution run independently of monitoring.
Native forming OHLC is combined with observed quotes; sampled or partial
candles carry explicit provenance/completeness flags. Missing history is never
invented. Missing/stale quotes produce NO TRADE with no current price. The
browser title provides no exchange timestamp. A price change or a fresh native
broker reply for the exact asset and current candle, with the same closing
price, verifies the observation. Lightweight native requests run each second
independently of bulk history refresh. A constant price can therefore remain
fresh when the broker confirms it. Polling the title or rereading cached bars
does not renew source timestamps; evidence older than 2 seconds blocks entries.
Receipt times are identified as observations, never exchange timestamps.
`[MarketData]` reports `native_forming_fresh_by_timeframe` for the native cache.
This is separate from the selected quote's freshness: matching the exact asset,
current candle and displayed price is still required. Its
`closed_history_managed_separately` field describes the refresh worker's scope;
it does not mean that completed candles have stopped updating.

`quote_status` and `closed_candle_status` report source freshness separately.
`data_status` requires both to be fresh; a fresh quote cannot hide missing or
outdated 30s/1m history. Both feeds remain required; minute history also supplies
the five-minute aggregation. `closed_candles` records expected and
observed bar times and history continuity. `entry_timing` reports the selected
completed entry candle's age and whether it is inside the existing five-second
entry window. Individual
live-refresh request timeouts are debug events; feed status changes and failed
bulk-history requests remain visible. Live refresh waits up to 1.5 seconds in
its independent task, without suspending the one-second decision monitor.
Native history requests also bound quote verification and browser transport,
with a whole-operation timeout of the configured response timeout plus 0.2
seconds. A stalled timeframe times out without discarding its completed
history; another timeframe can finish, and the next cycle retries missing data.
Caller cancellation clears request bookkeeping and still propagates normally.
Only candles already completed at the request's snapshot time are returned as
closed history. A reply crossing a candle boundary cannot promote a snapshot
that was still forming when requested.

A feed outage preserves completed history and known candle extremes. Forming
bars become provisional/partial until coverage is recovered. An incomplete
sampled bar is never promoted to a completed candle; fresh native OHLC can
restore coverage after an outage. Older closed snapshots still block entries
until the expected completed bar arrives.

BUY submits Up and SELL submits Down after the corresponding closed-candle
rules and five-minute direction pass. Both directions share the execution lock,
broker active-order check, pending-order journal and durable consumed-setup
identity that prevent duplicate entries. An unresolved demo order blocks
another order until broker settlement.
Setup identity includes the asset, entry period and candle timestamp, so 30-second
and one-minute candles starting at the same time remain distinct. It deliberately
excludes direction: switching from BUY to SELL cannot authorize another attempt
on an already consumed candle. The existing daily risk limits cover both sides
together.
The existing five-second confirmation-entry window is separate from the
one-second monitoring interval. Expiry uses the broker's duration field, not a
local candle-boundary timer or the time at which acknowledgement was read.
The entry deadline is also checked after browser preflight, immediately before
submission; an expired signal is rejected. Acknowledgement arriving after a
valid submission does not turn that order into a rejection.

## Environment and verification

```bash
conda activate tg_algo
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
```

The required packages are already installed in `tg_algo`. Disabling automatic
pytest plugin discovery avoids an unrelated globally inherited ROS plugin;
the project's tests do not need that plugin. Noninteractive commands can use
`conda run -n tg_algo` to select the same environment.

To inspect actual broker data without submitting orders:

```bash
python multi_agent_orchestrator.py --observe-only --max-runtime 65
```

Use the previous entry period with:

```bash
python multi_agent_orchestrator.py --entry-seconds 60 --observe-only --max-runtime 65
```

For the normal demo execution path, omit `--observe-only`. Shorter entries still
have to pass every strategy, data, risk and execution check before an order is
submitted. Restart an already running process to load the code and entry setting.

The runtime limit begins after login/setup. Automated tests use deterministic
market fixtures and local browser markup; they do not submit broker orders.

## Trade-frequency target

Ten to fifteen trades per hour is a target, not a forced entry quota. Each trade
still requires all confirmations, a fresh quote, a matching five-minute filter,
available risk capacity, and no unresolved demo order. The active default
maximum is 20 trades per day; this has not been increased to chase frequency.

Measure recorded opportunities and actual accepted orders without counting
repeated one-second evaluations of the same setup as new opportunities:

```bash
python scripts/analyze_monitoring_log.py logs/runs/unified_20261004_013438.log
```

The report describes the recorded interval and the rules that blocked entries.
It supports archived `confirmed_buy` and BUY-only `buy_evaluation` logs, as well
as records containing both `buy_evaluation` and `sell_evaluation`. Its
`no_trade_explanation` separates missing qualifying setups from data, timing,
5m filter, risk and execution blockers. Rejection counts deduplicate repeated
polls of the same asset, entry period, entry candle and diagnostic direction;
one candle can encounter multiple blockers. Failure and rejection counts use
`entry_evaluation`, so the opposite side's expected failures do not obscure the
selected setup. Timing uses the logged entry period; archived records without
that field retain their one-minute interpretation. A 30-second candle and a
one-minute candle with the same timestamp count separately. BUY and SELL
opportunities are also counted separately. `unique_confirmed_buy_setups` and
`unique_confirmed_sell_setups` report each side; `unique_confirmed_setups`
reports their total. `setup_counts_by_direction` includes each side's fresh,
timely and five-minute-filtered counts. The existing generic freshness/filter
summary fields retain their historical BUY-only meaning; their `total_` versions
include both directions. A logged filter pass is credited only to its requested
direction. `evaluated_entry_candles` counts physical candles once across both
sides; per-direction evaluation counts appear in `setup_counts_by_direction`.
Shorter entry candles do not guarantee more trades or profitable outcomes.
The report does not extrapolate a guaranteed hourly trade count or trading
performance.
An already running process must be restarted to load code changes; its existing
log continues to describe the version it started with.
