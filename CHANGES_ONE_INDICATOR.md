# One-indicator + live tick update

## Diagnosis (why prices/candles lagged)
- Live price came from polling `page.title()` once per second (CDP round trip); freshness was inferred from title changes.
- `_receive_market_frame` only handled history replies (event 10). Live quote frames were ignored, so the forming candle moved only when a history request answered.
- Closed candles arrived via a history fetch after close (3s timeout), then entries waited up to 5s: trades fired mid-candle.
- Strategy had ~15 gates (EMA20/50, EMA9/21, RSI, swings, 5m filter).

## Added (nothing existing removed)
- `bot/live_feed.py`: tick-driven price, 30s/60s forming candle, momentum (body, up/down ticks, velocity), instant rollover event at candle open. Broker history is merged as truth for closed candles.
- `bot/browser.py`: frame hook feeds `live_feed`; `get_current_quote` uses the fresh tick (title only re-verifies asset every 2s, old path kept as fallback). Login/asset selection untouched.
- `strategies/one_indicator.py`: EMA(9) only. BUY: last closed close > EMA and EMA rising; SELL mirror.
- `multi_agent_orchestrator.py`: `_one_indicator_loop` fires at the open of the new candle (timeframe = STRATEGY_ENTRY_SECONDS), payout cached off the critical path, same risk/lock/persistence/settlement path as before.
- `config/settings.py`: ONE_INDICATOR_ENABLED (default true), ONE_INDICATOR_EMA_PERIOD, ONE_INDICATOR_SLOPE_BARS, ONE_INDICATOR_ENTRY_WINDOW_SECONDS.
- Set `ONE_INDICATOR_ENABLED=false` to return to the old strategy.

## Verify on first run
Tick frame format (`{"e":1,"d":[{"p":pair,"q":price,"t":ts}]}`) is parsed from my knowledge of the broker feed and could not be tested live. Run once with `TICK_DEBUG=true`, check `logs/ws_frame_shapes.log` and the `[OneIndicator]` log (`processing_delay_ms`). If `ticks_seen` stays 0, send me that log file.
