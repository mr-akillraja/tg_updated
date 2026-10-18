# Follow-up fixes and verification — 2026-09-30

The follow-up corrects the remaining chart-display, restart accounting and strategy-performance reporting problems. The original native-data, shared-process and closed-candle fixes are documented in [the original report](bot_fix_report.md).

## What changed, file by file

| File | Problem and correction |
|---|---|
| [bot/chart_indicators.py](../bot/chart_indicators.py) | The old helper neither applied nor verified the exact strategy overlays. The replacement selects the chart interval (separately from trade duration), reads each saved indicator period, removes extra/duplicate indicators, and applies exact entry or trend profiles. Live testing exposed unmounted chart legends being mistaken for an empty indicator list. Setup now renders the technical-analysis controls before reading saved instances. Success requires actual UI readback; repeated configuration does not add duplicates. |
| [multi_agent_orchestrator.py](../multi_agent_orchestrator.py) | Applies the verified chart profile at startup; adds --chart-view entry or trend. Gate logs identify live quote separately from closed prices and include local ISO candle opening/closing times. Removed misleading intermediate seed logs reporting an empty 1m builder before its seed had run. Durable journal snapshots now drive daily counts, loss budgets and loss cooldowns. Both risk components use the same existing effective limits: 20 accepted trades/day and 2000 gross loss plus pending exposure. Session counts and today's account/strategy results are separately labelled. |
| [bot/risk.py](../bot/risk.py) | Restart no longer starts independent counters at zero or resets the journal. Reads current and archived journals, reserves pending exposure, restores loss streaks and reports account statistics separately from strategy statistics. Persists settlement timestamps and keeps backward compatibility with older CSV files. Per-trade risk applies to normal execution as well as training. A rejected oversized proposed trade does not permanently latch a halt. Duplicate/unknown journal identities fail closed. |
| [bot/browser.py](../bot/browser.py) | Added explicit broker evidence for an empty demo Fixed Time account. An empty DOM list alone is insufficient: the broker's “No active Fixed Time trades or orders on this account” message, no active cards, and a verified demo account are all required. |
| [agents/agent_pipeline_supervisor.py](../agents/agent_pipeline_supervisor.py) | Circuit breaker now keeps an actual rolling window of 20 outcomes. Restores outcomes in settlement order and preserves the remaining cooldown across restart. Existing loss-rate and cooldown thresholds are unchanged. |
| [agents/agent_asset_selector.py](../agents/agent_asset_selector.py) | Excludes records explicitly labelled DEMO_EXECUTION_TEST from strategy win-rate calculations, including archived records. |
| [agents/agent_rl_learning.py](../agents/agent_rl_learning.py) | Excludes mechanical execution checks from replay transitions. |
| [scripts/strategy_and_analysis.py](../scripts/strategy_and_analysis.py) | Excludes mechanical execution checks before selecting the strategy analysis sample. |
| [scripts/verify_demo_order.py](../scripts/verify_demo_order.py) | Uses the same default risk limits and broker flat-account reconciliation. Persists the DEMO_EXECUTION_TEST label in accepted-order recovery state before journaling, so an interrupted write cannot turn a test into an apparent strategy trade after restart. |

Regression additions/updates: test_risk_journal.py, test_strategy_performance.py, test_circuit_restore.py, test_remaining_accounting.py, test_execution_state.py, test_chart_display.py and test_broker_trade_cards.py.

## Old unresolved records

Reading all archived journals exposed 38 old Fixed Time records from May still marked PENDING, totalling 24,400 units of apparent open exposure. The live broker explicitly reported no active Fixed Time trades or orders. With fresh evidence from that verified demo account, previous-day FTT rows are no longer treated as currently open positions for that session.

Their results remain unknown. No WIN, LOSS or P&L was invented and no historical CSV row was deleted or rewritten by this reconciliation. Today's pending records and Forex records are not released. Every restart requires fresh broker evidence again.

## Chart and strategy comparison

The latest uploaded screenshot showed 1m Bollinger Bands (20, 2). That overlay cannot verify the strategy's EMA/RSI conditions. The corrected entry display is 1m EMA9/EMA21 plus RSI14; the trend display is 30s EMA20/EMA50. Both profiles passed actual browser readback, and entry configuration was repeated successfully without duplicates.

- [Verified entry screenshot](chart_entry_verified.png)
- [Verified trend screenshot](chart_trend_verified.png)

A screenshot's live quote can differ from the log's last closed candle. Logs now expose both prices and the precise candle times. Indicator values use native closed broker candles; chart lines may include the forming candle and a longer initialization history. Correct display periods do not prove pixel-for-pixel numeric equality of those two different timestamps.

No EMA periods, RSI bands, swing lookbacks, confirmation thresholds or clearance thresholds were changed. The existing disabled requirement for strict 30s HH+HL/LH+LL structure remains disabled and is explicitly logged as trend_structure_required=false. Existing entry swing rules remain enforced.

## Verification

The final real-candle evidence and full numerical gate payloads are in [follow-up verification](followup_verification.md) and [machine-readable evidence](followup_verification.json). Native 30s and 1m history are verified separately; there is no M1 upsampling.

The final suite passed **139 tests**. The strategy-enabled session completed ten consecutive 30s closes from 03:39:30 through 03:44:00 IST, with 20 BUY/SELL evaluations, no duplicated snapshot pairs and no qualifying signal. No strategy order was submitted.

A separate 200-unit, 60-second demo BUY check was then accepted automatically as broker order **12534106199** at 03:44:27.016 and settled automatically at 03:45:27.842. The actual result was **LOSS, -200 demo units**. This was a mechanical execution check, not a qualifying strategy signal. Both the accepted-order state and journal carry DEMO_EXECUTION_TEST.

After settlement, a fresh RiskManager restored one accepted/settled account trade and 200 gross loss, while strategy settled trades and strategy consecutive losses remained zero. The unresolved-order file is null. Evidence: [execution and restored accounting](followup_demo_execution.json), [original execution log](../logs/runs/unified_20260930_034414.log).

Both bounded sessions exited successfully. No bot was left running; the normal start command is below.

An entry is permitted only when all existing rules pass. A run with zero qualifying signals cannot demonstrate strategy profitability, and mechanical demo execution checks are reported separately.

## Running the corrected bot

From the project directory:

~~~bash
conda activate tg_algo
python multi_agent_orchestrator.py
~~~

The default display is the 1m entry chart. To display the trend chart instead:

~~~bash
python multi_agent_orchestrator.py --chart-view trend
~~~

Both display choices still evaluate both directions on shared 30s/1m data. Run only one instance; the process lock prevents duplicate cooperating instances. Execution requires a verified demo account.
