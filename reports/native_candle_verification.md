# Actual broker candle-close verification

Source: [complete run log](../logs/runs/unified_20260929_025743.log). Times are IST on 2026-09-29.

Completed 12 consecutive real 30-second closes and six distinct closed 1-minute candles. Both gates saw the same snapshot at each event: 24 numerical evaluations, no duplicate poll evaluations. Neither direction passed every rule, so this strategy run submitted zero orders.

Native history requested separately for 30s and 1m: 180 bars each. The trend builder retained 180; the existing entry builder capacity retained 70. The counts below are the actual retained closed candles. No M1 upsampling.

```text
2026-09-29 02:58:31,849 [INFO] multi_agent_orchestrator — [Seed] {"asset": "EUR/USD OTC", "closed_candles": 180, "m1_upsampling": false, "native_history_available": true, "required_closed": 53, "source": "broker_native_ws", "timeframe_seconds": 30}
2026-09-29 02:58:32,126 [INFO] multi_agent_orchestrator — [Seed] {"asset": "EUR/USD OTC", "closed_candles": 70, "m1_upsampling": false, "native_history_available": true, "required_closed": 21, "source": "broker_native_ws", "timeframe_seconds": 60}
```

| Event | Evaluated at IST | 30s close | EMA20 | EMA50 | RSI14 | Direction | BUY first failure | SELL first failure |
|---:|---|---:|---:|---:|---:|---|---|---|
| 1 | 02:59:00,867 | 1.09530 | 1.0952677 | 1.0955178 | 43.37 | rising | 30s_ema20_vs_ema50 | 30s_close_vs_ema20 |
| 2 | 02:59:30,612 | 1.09525 | 1.0952660 | 1.0955072 | 43.37 | rising | 30s_close_vs_ema20 | 1m_close_breaks_swing |
| 3 | 03:00:00,918 | 1.09544 | 1.0952826 | 1.0955045 | 47.34 | rising | 30s_ema20_vs_ema50 | 30s_close_vs_ema20 |
| 4 | 03:00:30,519 | 1.09533 | 1.0952871 | 1.0954979 | 47.34 | rising | 30s_ema20_vs_ema50 | 30s_close_vs_ema20 |
| 5 | 03:01:00,980 | 1.09518 | 1.0952769 | 1.0954855 | 41.57 | falling | 30s_close_vs_ema20 | 30s_ema_slope_3_candles |
| 6 | 03:01:30,719 | 1.09512 | 1.0952620 | 1.0954711 | 41.57 | falling | 30s_close_vs_ema20 | 1m_pullback_close_vs_ema21 |
| 7 | 03:02:01,076 | 1.09511 | 1.0952475 | 1.0954569 | 40.01 | falling | 30s_close_vs_ema20 | 1m_pullback_close_vs_ema21 |
| 8 | 03:02:30,768 | 1.09524 | 1.0952468 | 1.0954485 | 40.01 | falling | 30s_close_vs_ema20 | 1m_pullback_close_vs_ema21 |
| 9 | 03:03:01,189 | 1.09547 | 1.0952680 | 1.0954493 | 49.47 | rising | 30s_ema20_vs_ema50 | 30s_close_vs_ema20 |
| 10 | 03:03:30,880 | 1.09554 | 1.0952939 | 1.0954528 | 49.47 | rising | 30s_ema20_vs_ema50 | 30s_close_vs_ema20 |
| 11 | 03:04:01,188 | 1.09560 | 1.0953231 | 1.0954586 | 52.33 | rising | 30s_ema20_vs_ema50 | 30s_close_vs_ema20 |
| 12 | 03:04:30,736 | 1.09559 | 1.0953485 | 1.0954636 | 52.33 | rising | 30s_ema20_vs_ema50 | 30s_close_vs_ema20 |

The following excerpts are generated from the real JSON log lines, rounded only for readability. Full precision, all failed rules, pullback close/EMA pairs, swing prices and timestamps are in [the JSON evidence](native_candle_verification.json).

```text
2026-09-29 02:59:00,867 [BuyOnlyGate] event=1 close=1.09530 ema20=1.0952677 ema50=1.0955178 ema20_3ago=1.0952984 ema50_3ago=1.0955616 entry_close=1.09530 ema9=1.0952454 ema21=1.0954375 structure=LH+LL entry_structure=LH+LL rsi=43.37 rsi_dir=rising -> FAIL(30s_ema20_vs_ema50)
2026-09-29 02:59:00,868 [SellOnlyGate] event=1 close=1.09530 ema20=1.0952677 ema50=1.0955178 ema20_3ago=1.0952984 ema50_3ago=1.0955616 entry_close=1.09530 ema9=1.0952454 ema21=1.0954375 structure=LH+LL entry_structure=LH+LL rsi=43.37 rsi_dir=rising -> FAIL(30s_close_vs_ema20)
2026-09-29 02:59:30,612 [BuyOnlyGate] event=2 close=1.09525 ema20=1.0952660 ema50=1.0955072 ema20_3ago=1.0952700 ema50_3ago=1.0955395 entry_close=1.09530 ema9=1.0952454 ema21=1.0954375 structure=LH+LL entry_structure=LH+LL rsi=43.37 rsi_dir=rising -> FAIL(30s_close_vs_ema20)
2026-09-29 02:59:30,612 [SellOnlyGate] event=2 close=1.09525 ema20=1.0952660 ema50=1.0955072 ema20_3ago=1.0952700 ema50_3ago=1.0955395 entry_close=1.09530 ema9=1.0952454 ema21=1.0954375 structure=LH+LL entry_structure=LH+LL rsi=43.37 rsi_dir=rising -> FAIL(1m_close_breaks_swing)
2026-09-29 03:00:00,918 [BuyOnlyGate] event=3 close=1.09544 ema20=1.0952826 ema50=1.0955045 ema20_3ago=1.0952643 ema50_3ago=1.0955265 entry_close=1.09544 ema9=1.0952844 ema21=1.0954374 structure=LH+LL entry_structure=LH+LL rsi=47.34 rsi_dir=rising -> FAIL(30s_ema20_vs_ema50)
2026-09-29 03:00:00,918 [SellOnlyGate] event=3 close=1.09544 ema20=1.0952826 ema50=1.0955045 ema20_3ago=1.0952643 ema50_3ago=1.0955265 entry_close=1.09544 ema9=1.0952844 ema21=1.0954374 structure=LH+LL entry_structure=LH+LL rsi=47.34 rsi_dir=rising -> FAIL(30s_close_vs_ema20)
2026-09-29 03:00:30,519 [BuyOnlyGate] event=4 close=1.09533 ema20=1.0952871 ema50=1.0954979 ema20_3ago=1.0952677 ema50_3ago=1.0955179 entry_close=1.09544 ema9=1.0952844 ema21=1.0954374 structure=LH+LL entry_structure=LH+LL rsi=47.34 rsi_dir=rising -> FAIL(30s_ema20_vs_ema50)
2026-09-29 03:00:30,519 [SellOnlyGate] event=4 close=1.09533 ema20=1.0952871 ema50=1.0954979 ema20_3ago=1.0952677 ema50_3ago=1.0955179 entry_close=1.09544 ema9=1.0952844 ema21=1.0954374 structure=LH+LL entry_structure=LH+LL rsi=47.34 rsi_dir=rising -> FAIL(30s_close_vs_ema20)
2026-09-29 03:01:00,980 [BuyOnlyGate] event=5 close=1.09518 ema20=1.0952769 ema50=1.0954855 ema20_3ago=1.0952660 ema50_3ago=1.0955074 entry_close=1.09518 ema9=1.0952635 ema21=1.0954141 structure=LH+LL entry_structure=LH+LL rsi=41.57 rsi_dir=falling -> FAIL(30s_close_vs_ema20)
2026-09-29 03:01:00,981 [SellOnlyGate] event=5 close=1.09518 ema20=1.0952769 ema50=1.0954855 ema20_3ago=1.0952660 ema50_3ago=1.0955074 entry_close=1.09518 ema9=1.0952635 ema21=1.0954141 structure=LH+LL entry_structure=LH+LL rsi=41.57 rsi_dir=falling -> FAIL(30s_ema_slope_3_candles)
2026-09-29 03:01:30,719 [BuyOnlyGate] event=6 close=1.09512 ema20=1.0952620 ema50=1.0954711 ema20_3ago=1.0952826 ema50_3ago=1.0955048 entry_close=1.09518 ema9=1.0952635 ema21=1.0954141 structure=LH+LL entry_structure=LH+LL rsi=41.57 rsi_dir=falling -> FAIL(30s_close_vs_ema20)
2026-09-29 03:01:30,720 [SellOnlyGate] event=6 close=1.09512 ema20=1.0952620 ema50=1.0954711 ema20_3ago=1.0952826 ema50_3ago=1.0955048 entry_close=1.09518 ema9=1.0952635 ema21=1.0954141 structure=LH+LL entry_structure=LH+LL rsi=41.57 rsi_dir=falling -> FAIL(1m_pullback_close_vs_ema21)
2026-09-29 03:02:01,076 [BuyOnlyGate] event=7 close=1.09511 ema20=1.0952475 ema50=1.0954569 ema20_3ago=1.0952871 ema50_3ago=1.0954978 entry_close=1.09511 ema9=1.0952328 ema21=1.0953868 structure=LH+LL entry_structure=LH+LL rsi=40.01 rsi_dir=falling -> FAIL(30s_close_vs_ema20)
2026-09-29 03:02:01,076 [SellOnlyGate] event=7 close=1.09511 ema20=1.0952475 ema50=1.0954569 ema20_3ago=1.0952871 ema50_3ago=1.0954978 entry_close=1.09511 ema9=1.0952328 ema21=1.0953868 structure=LH+LL entry_structure=LH+LL rsi=40.01 rsi_dir=falling -> FAIL(1m_pullback_close_vs_ema21)
2026-09-29 03:02:30,768 [BuyOnlyGate] event=8 close=1.09524 ema20=1.0952468 ema50=1.0954485 ema20_3ago=1.0952769 ema50_3ago=1.0954855 entry_close=1.09511 ema9=1.0952328 ema21=1.0953868 structure=mixed entry_structure=LH+LL rsi=40.01 rsi_dir=falling -> FAIL(30s_close_vs_ema20)
2026-09-29 03:02:30,769 [SellOnlyGate] event=8 close=1.09524 ema20=1.0952468 ema50=1.0954485 ema20_3ago=1.0952769 ema50_3ago=1.0954855 entry_close=1.09511 ema9=1.0952328 ema21=1.0953868 structure=mixed entry_structure=LH+LL rsi=40.01 rsi_dir=falling -> FAIL(1m_pullback_close_vs_ema21)
2026-09-29 03:03:01,189 [BuyOnlyGate] event=9 close=1.09547 ema20=1.0952680 ema50=1.0954493 ema20_3ago=1.0952620 ema50_3ago=1.0954712 entry_close=1.09547 ema9=1.0952802 ema21=1.0953943 structure=mixed entry_structure=LH+LL rsi=49.47 rsi_dir=rising -> FAIL(30s_ema20_vs_ema50)
2026-09-29 03:03:01,190 [SellOnlyGate] event=9 close=1.09547 ema20=1.0952680 ema50=1.0954493 ema20_3ago=1.0952620 ema50_3ago=1.0954712 entry_close=1.09547 ema9=1.0952802 ema21=1.0953943 structure=mixed entry_structure=LH+LL rsi=49.47 rsi_dir=rising -> FAIL(30s_close_vs_ema20)
2026-09-29 03:03:30,880 [BuyOnlyGate] event=10 close=1.09554 ema20=1.0952939 ema50=1.0954528 ema20_3ago=1.0952475 ema50_3ago=1.0954569 entry_close=1.09547 ema9=1.0952802 ema21=1.0953943 structure=mixed entry_structure=LH+LL rsi=49.47 rsi_dir=rising -> FAIL(30s_ema20_vs_ema50)
2026-09-29 03:03:30,880 [SellOnlyGate] event=10 close=1.09554 ema20=1.0952939 ema50=1.0954528 ema20_3ago=1.0952475 ema50_3ago=1.0954569 entry_close=1.09547 ema9=1.0952802 ema21=1.0953943 structure=mixed entry_structure=LH+LL rsi=49.47 rsi_dir=rising -> FAIL(30s_close_vs_ema20)
2026-09-29 03:04:01,188 [BuyOnlyGate] event=11 close=1.09560 ema20=1.0953231 ema50=1.0954586 ema20_3ago=1.0952468 ema50_3ago=1.0954484 entry_close=1.09560 ema9=1.0953442 ema21=1.0954131 structure=mixed entry_structure=mixed rsi=52.33 rsi_dir=rising -> FAIL(30s_ema20_vs_ema50)
2026-09-29 03:04:01,188 [SellOnlyGate] event=11 close=1.09560 ema20=1.0953231 ema50=1.0954586 ema20_3ago=1.0952468 ema50_3ago=1.0954484 entry_close=1.09560 ema9=1.0953442 ema21=1.0954131 structure=mixed entry_structure=mixed rsi=52.33 rsi_dir=rising -> FAIL(30s_close_vs_ema20)
2026-09-29 03:04:30,736 [BuyOnlyGate] event=12 close=1.09559 ema20=1.0953485 ema50=1.0954636 ema20_3ago=1.0952680 ema50_3ago=1.0954491 entry_close=1.09560 ema9=1.0953442 ema21=1.0954131 structure=mixed entry_structure=mixed rsi=52.33 rsi_dir=rising -> FAIL(30s_ema20_vs_ema50)
2026-09-29 03:04:30,736 [SellOnlyGate] event=12 close=1.09559 ema20=1.0953485 ema50=1.0954636 ema20_3ago=1.0952680 ema50_3ago=1.0954491 entry_close=1.09560 ema9=1.0953442 ema21=1.0954131 structure=mixed entry_structure=mixed rsi=52.33 rsi_dir=rising -> FAIL(30s_close_vs_ema20)
```
