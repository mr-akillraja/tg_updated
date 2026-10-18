# Trading bot correction and verification — 2026-09-29

The data and gate implementation bugs were corrected. Native EUR/USD OTC 30-second history is available from the authenticated broker WebSocket and is now requested at its actual resolution. BUY and SELL run in one process using the same closed-candle snapshots. The corrected browser execution path also passed a real demo-account order acknowledgement and settlement check.

The 12-close strategy run produced no fully qualifying signal. The two separate demo execution tests below verify browser operation, not the strategy's profitability or its ability to produce a signal in that particular market window.

## What was wrong and what changed

| File | Defect and correction |
|---|---|
| [agents/agent_multi_timeframe.py](../agents/agent_multi_timeframe.py) | The old seed path used M1 rows to construct purported 30s history, including selecting every other M1 row and inventing interval timestamps. Builders also exposed their forming candle. Seeding now requires matching timeframe metadata, preserves actual broker timestamps, validates OHLC, and retains only completed bars. Getters and snapshots exclude the forming bar. Live sampling discards the first partial bar and invalidates gaps; it never fills missing candles. RSI14 now uses Wilder gain/loss smoothing. |
| [data/fetcher.py](../data/fetcher.py) | OTC could be mapped to ordinary Yahoo forex, and failed downloads could produce random fallback candles. Unsupported OTC/subminute requests now raise an explicit data-unavailable condition. Synthetic fallback data was removed. |
| [bot/browser.py](../bot/browser.py) | Added native broker candle requests with separate 30s/60s resolution, exact asset identity, validated OHLC and closed timestamps. Updated login/session, asset, amount, duration, payout and trade-button selectors to the observed platform. Orders require a unique broker trade ID matching asset, direction, amount and duration. Settlement requires that exact ID and the broker's final P&L. The live test exposed another issue: active and settled cards both use `deal-history-card-ftt`; active orders now come from `active-deal-list`, rather than being discarded because of the card name. Projected P&L on an active card is never treated as settlement. |
| [strategies/closed_candle.py](../strategies/closed_candle.py) — new | Centralized both directions in a deterministic evaluation over the same snapshots. Corrected the three-bars-ago index from two bars back to three; pullback rules compare each candle's close against its own EMA21, rather than wick extremes against the latest EMA; the previous-ten-candle range excludes the confirmation candle; resistance clearance compares a price distance with candle range. Both gates return all numerical diagnostics and all failed rules, even when the first rule fails. |
| [multi_agent_orchestrator.py](../multi_agent_orchestrator.py) | Replaced the separate BUY-only path with both gates evaluated once per new closed-candle snapshot. Both share one browser, asset selection and history source. Native history refreshes only when a new close is due. An unchanged 1m confirmation cannot generate another order at the intervening 30s close. Stale confirmations/snapshots are blocked. Separate seed-source/count JSON lines and numerical gate JSON lines replace poll-level pass/fail spam. Submission state is persisted before execution; accepted orders survive restart; unresolved outcomes block further entries. Startup no longer clears the trade journal. |
| [bot/process_lock.py](../bot/process_lock.py) — new | A user-wide process lock prevents two cooperating copies from opening separate bot sessions or issuing competing trade clicks. The CLI acquires it before initializing mutable trading state. An asynchronous execution lock serializes submissions inside the process. |
| [agents/agent_pipeline_supervisor.py](../agents/agent_pipeline_supervisor.py) | Removed automatic retry of FTT submissions. A timeout after a click is treated as uncertain and blocks another order. Risk approval remains required. Recovery verifies the selected asset and invalidates stale candle state. |
| [bot/risk.py](../bot/risk.py) | Made journal writes atomic and trade insertion/settlement idempotent. Unknown or conflicting settlements cannot silently change P&L. Pending trades are retained across rollover/reset. |
| [bot/chart_indicators.py](../bot/chart_indicators.py) | Removed an accidentally duplicated bot executable; retained the chart UI helpers. |
| [deploy/multi_agent.service](../deploy/multi_agent.service) | Updated the service definition to the main workspace and the `tg_algo` interpreter. The service was not installed or enabled by this work. |
| `../tg_algo-main (Copy)/multi_agent_orchestrator.py` | Replaced the second SELL-only executable with a launcher for the main unified implementation. Its previous source is preserved in that copy's `backups/pre_unification_sell.py.txt`. The shared process lock prevents running the two launchers concurrently. |
| [scripts/verify_demo_order.py](../scripts/verify_demo_order.py) — new | Explicit, separately labelled demo-only execution check. Verifies the account, applies risk approval, submits once, requires broker acknowledgement and settlement, and retains an unresolved marker if confirmation fails. It does not alter or bypass the production gate rules. |

Regression coverage is in `tests/test_candle_data.py`, `test_closed_strategy.py`, `test_structure_gate.py`, `test_asset_lock.py`, `test_execution_state.py`, `test_risk_journal.py`, `test_browser_execution.py`, `test_native_refresh.py`, and `test_broker_trade_cards.py`. The last test uses a real local Playwright page reproducing the broker's shared active/history card markup. The final complete suite passed: **86 tests in 2.56 seconds** in `tg_algo`.

## Preserved strategy settings

- Trend: 30s EMA20/EMA50; entry: 1m EMA9/EMA21; RSI14.
- BUY RSI band 50–65; SELL RSI band 35–50; existing two-bars-left/two-bars-right swing confirmation lookback.
- Existing confirmation range cap of 2x, BUY obstacle clearance 1.5x and SELL clearance 1x.
- The original trend gates had strict swing-structure enforcement disabled. Structure is reported numerically without silently enabling a new mandatory filter. Existing entry swing rules, including the SELL fallback, remain in place.
- Gates require enough closed data to evaluate mature indicators, including EMA50 three candles ago: 53 trend candles and 21 entry candles. If native history is unavailable, no M1-derived 30s seed is used; the builder warms up from actual browser observations. Those are explicitly labelled sampled observations, not tick-complete native OHLC.

## Real candle verification

See [all 24 numerical BUY/SELL excerpts and the 12-close table](native_candle_verification.md), [full-precision JSON](native_candle_verification.json), and [original run log](../logs/runs/unified_20260929_025743.log).

The session ran from 02:57:43 to 03:04:31 IST. Evaluations covered twelve consecutive 30s closes from 02:59:00 to 03:04:30 and six distinct 1m closes. Both directions used identical timestamps at each event. There were zero duplicated gate keys and zero accepted strategy orders.

Actual seed source: `broker_native_ws`, with `native_history_available=true` and `m1_upsampling=false` for each timeframe. The 30s builder retained 180 closed native candles. The 1m request also fetched 180; its existing capacity retained 70. The logs report the actual retained counts separately.

For example, at 02:59:30 the SELL trend checks passed, but `1m_close_breaks_swing` failed. Later events failed different conditions. Thus repeated failures are no longer an opaque, fixed first-stage result; the numerical evidence identifies the conditions actually preventing each entry. Repeated polls against one candle are not independent trading opportunities.

## Demo order execution verification

These were explicitly labelled mechanical demo tests, each for 200 demo units and 60 seconds on EUR/USD OTC.

| Test | Broker trade ID | Actual result | Verification |
|---|---|---|---|
| BUY | `12531898136` | WIN, +170 demo units | Click reached the broker at 03:10:02.814; closed at 03:11:02.814. Initial acknowledgement timed out because active cards were incorrectly filtered. No retry was made while uncertain. The exact broker record was subsequently read, verified, journaled, and reconciled. |
| SELL, after the selector fix | `12531912474` | WIN, +170 demo units | Accepted automatically at 03:24:02.623 and settlement recorded automatically at 03:25:03.900. Process exited successfully. |

Evidence: [first order's exact broker record](demo_order_reconciliation.json), [reconciliation log](../logs/runs/unified_20260929_032330.log), and [successful automatic execution/settlement log](../logs/runs/unified_20260929_032351.log).

```text
2026-09-29 03:24:02,623 [INFO] browser — [Order] accepted_id=12531912474 side=SELL
2026-09-29 03:25:03,900 [INFO] demo_execution_test — [ExecutionSmokeTest] {"result": "WIN", "pnl": 170.0, "source": "platform", "order_id": "12531912474", "status": "settled"}
```

Both orders are recorded as settled; `logs/execution_state.json` is `null`. The bounded verification sessions have stopped. No real-account trade was placed.

## Screenshot comparison and running the bot

The uploaded 30s screenshot displays EMA9 and EMA21, whereas the requested trend gate uses EMA20 and EMA50. For a visual comparison, set the 30s chart overlays to 20/50 and the 1m entry chart to 9/21 with RSI14. Chart overlays do not drive the bot's indicators; the bot computes them from the native candle series. Finite history and the chart's indicator initialization may also cause small numerical differences.

Start only the unified bot from the main workspace:

```bash
conda activate tg_algo
python multi_agent_orchestrator.py
```

The existing default stake is 200 demo units. For another bounded inspection without placing orders:

```bash
python multi_agent_orchestrator.py --observe-only --max-candle-closes 12 --max-runtime 480
```
