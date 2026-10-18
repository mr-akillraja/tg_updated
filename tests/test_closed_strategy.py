"""Regression checks use explicit fixtures, never broker/live data."""
import copy
import json
import math
from datetime import datetime

import pytest

from strategies.closed_candle import confirmed_swings, ema, evaluate_both, evaluate_demo_continuation, rsi_series


def candles(closes, period=60):
    return [dict(open=c, high=c + .15, low=c - .15, close=c,
                 ts=2400 - len(closes) * period + index * period)
            for index, c in enumerate(closes)]


def buy_fixture():
    trend = candles([101 + index * .05 for index in range(60)], 30)
    entry = candles([100 + 5 * math.sin(index * math.pi / 3) for index in range(30)]
                    + [101, 102, 101.8, 102.5, 103, 102.7, 102.9, 103.5, 103.2, 104])
    entry[35]["low"] = 101.8
    return trend, entry


def short_entry_fixture():
    """One real OHLC sequence satisfies both trend and entry rules at 30s."""
    closes = ([98 + index * .01 for index in range(20)]
              + [100 + 5 * math.sin(index * math.pi / 3) for index in range(30)]
              + [101, 102, 101.8, 102.5, 103, 102.7, 102.9, 103.5, 103.2, 104])
    bars = candles(closes, 30)
    bars[-5]["low"] = 101.8
    return bars


def mirror(bars):
    return [dict(open=210 - c["open"], high=210 - c["low"], low=210 - c["high"],
                 close=210 - c["close"], ts=c["ts"]) for c in bars]


def demo_fixture(direction="BUY"):
    closes = [100.0228, 100.0866, 100.0978, 100.0062, 99.897, 99.9879, 100.0919, 100.1909,
              100.2594, 100.2556, 100.3227, 100.2321, 100.3219, 100.4186, 100.3387,
              100.4583, 100.4934, 100.4802, 100.4433, 100.523, 100.464, 100.3597,
              100.3553, 100.2586, 100.2556, 100.189, 100.1861, 100.1441, 100.2575,
              100.3718, 100.4016, 100.4107, 100.528, 100.4636, 100.3979, 100.2724,
              100.2939, 100.3634, 100.3138, 100.1831, 100.2602, 100.1878, 100.2038,
              100.2829, 99.96, 100.04, 100.0, 100.12, 100.1, 100.28, 100.26, 100.38,
              100.34, 100.46, 100.42, 100.56, 100.52, 100.66, 100.62, 100.78]
    if direction == "SELL":
        closes = [200.0 - close for close in closes]
        closes = [max(0.0, c) for c in closes]
    candles_list = []
    for index, close in enumerate(closes):
        spread = 0.12
        open_ = close + (0.04 + (index % 5) * 0.01) if direction == "SELL" else close - (0.04 + (index % 5) * 0.01)
        high = close + spread
        low = close - spread
        candles_list.append(dict(open=open_, high=high, low=low, close=close,
                                ts=2400 - len(closes) * 30 + index * 30))
    return candles_list


def test_demo_strategy_buy_and_sell_are_valid_continuations():
    buy_bars = demo_fixture("BUY")
    buy = evaluate_demo_continuation(buy_bars, buy_bars, 30)["BUY"]
    assert buy["passed"], buy["failed_rules"]
    assert 45 <= buy["rsi"] <= 75
    assert buy["ema9"] > buy["ema21"]

    sell_bars = demo_fixture("SELL")
    sell = evaluate_demo_continuation(sell_bars, sell_bars, 30)["SELL"]
    assert sell["passed"], sell["failed_rules"]
    assert 25 <= sell["rsi"] <= 65
    assert sell["ema9"] < sell["ema21"]


def test_demo_strategy_rejects_neutral_and_missing_history():
    bars = demo_fixture("BUY")
    neutral = copy.deepcopy(bars)
    neutral[-1]["close"] = neutral[-1]["open"] = neutral[-1]["low"] = neutral[-1]["high"] = 100.0
    result = evaluate_demo_continuation(neutral, neutral, 30)
    assert not result["BUY"]["passed"]
    assert result["BUY"]["reason"] in {"ema9_vs_ema21", "ema9_touch_within_tolerance",
                                       "latest_candle_is_bullish", "rsi14_band", "rsi14_rising"}
    assert evaluate_demo_continuation([], [], 30)["BUY"]["status"] == "insufficient_data"


def test_complete_buy_and_sell_setups_pass_with_original_bands():
    trend, entry = buy_fixture()
    result = evaluate_both(trend, entry)
    assert result["BUY"]["passed"], result["BUY"]["failed_rules"]
    assert not result["SELL"]["passed"]
    assert 50 <= result["BUY"]["rsi"] <= 65
    result = evaluate_both(mirror(trend), mirror(entry))
    assert result["SELL"]["passed"], result["SELL"]["failed_rules"]
    assert not result["BUY"]["passed"]
    assert 35 <= result["SELL"]["rsi"] <= 50


def test_explicit_minute_entry_retains_default_results():
    trend, entry = buy_fixture()
    assert evaluate_both(trend, entry, 60) == evaluate_both(trend, entry)


def test_same_completed_30s_history_can_confirm_trend_and_entry():
    bars = short_entry_fixture()
    before = copy.deepcopy(bars)
    buy = evaluate_both(bars, bars, 30)["BUY"]
    assert buy["passed"], buy["failed_rules"]
    assert buy["trend_timeframe_seconds"] == buy["entry_timeframe_seconds"] == 30
    assert buy["entry_ts"] == buy["trend_ts"] == bars[-1]["ts"]
    assert buy["entry_close_at"] == buy["trend_close_at"]
    assert datetime.fromisoformat(buy["entry_close_at"]).timestamp() == bars[-1]["ts"] + 30
    assert buy["required_rsi_band"] == [50., 65.]
    assert 50 <= buy["rsi"] <= 65 and buy["rsi_dir"] == "rising"
    assert buy["required_obstacle_clearance"] == pytest.approx(1.5 * buy["confirmation_range"])
    assert buy["entry_count"] == buy["trend_count"] == 60
    sell_bars = mirror(bars)
    sell = evaluate_both(sell_bars, sell_bars, 30)["SELL"]
    assert sell["passed"], sell["failed_rules"]
    assert sell["required_rsi_band"] == [35., 50.]
    assert bars == before


@pytest.mark.parametrize("change,expected_rule", [
    ("pullback", "pullback_close_vs_ema21"),
    ("range", "confirmation_range"),
    ("breakout", "close_breaks_previous_candle"),
    ("rsi", "rsi14_band"),
])
def test_short_entry_retains_minute_rule_thresholds_and_labels(change, expected_rule):
    trend, minute_entry = buy_fixture()
    if change == "pullback":
        minute_entry[-3].update(open=99., high=99.1, low=98.9, close=99.)
    elif change == "range":
        minute_entry[-1]["high"] = minute_entry[-1]["close"] + 2.
    elif change == "breakout":
        minute_entry[-2]["high"] = minute_entry[-1]["close"] + .1
    else:
        minute_entry[-1].update(open=130., high=130.15, low=129.85, close=130.)
    short_entry = [dict(bar, ts=2400 - len(minute_entry) * 30 + index * 30)
                   for index, bar in enumerate(minute_entry)]
    minute = evaluate_both(trend, minute_entry)["BUY"]
    short = evaluate_both(trend, short_entry, 30)["BUY"]
    assert not minute["passed"] and not short["passed"]
    assert f"30s_{expected_rule}" in short["failed_rules"]
    assert short["failed_rules"] == [rule.replace("1m_", "30s_") for rule in minute["failed_rules"]]
    for metric in ("ema9", "ema21", "rsi", "rsi_previous", "confirmation_range",
                   "avg_prev_10_range", "required_obstacle_clearance"):
        assert short[metric] == minute[metric]


@pytest.mark.parametrize("period", [0, 15, 45, 300, None, "30"])
def test_unsupported_entry_period_is_rejected(period):
    with pytest.raises(ValueError, match="Entry confirmation requires"):
        evaluate_both([], [], period)


def test_wick_beyond_ema21_does_not_claim_a_close_beyond_ema21():
    trend, entry = buy_fixture()
    entry[35]["low"] = 100.0
    result = evaluate_both(trend, entry)["BUY"]
    assert entry[35]["low"] < result["pullback_closes_ema21"][0]["ema21"]
    assert result["passed"], result["failed_rules"]
    result = evaluate_both(mirror(trend), mirror(entry))["SELL"]
    assert result["passed"], result["failed_rules"]


def test_real_pullback_close_beyond_ema21_rejects_both_directions():
    trend, entry = buy_fixture()
    entry[-3].update(open=99.0, high=99.1, low=98.9, close=99.0)
    assert "1m_pullback_close_vs_ema21" in evaluate_both(trend, entry)["BUY"]["failed_rules"]
    result = evaluate_both(mirror(trend), mirror(entry))["SELL"]
    assert "1m_pullback_close_vs_ema21" in result["failed_rules"]


def test_pullback_uses_each_candles_own_ema21():
    trend, _ = buy_fixture()
    entry = candles([100 + index * .1 for index in range(39)] + [130.0])
    result = evaluate_both(trend, entry)["BUY"]
    assert entry[-5]["close"] < result["ema21"]
    assert "1m_pullback_close_vs_ema21" not in result["failed_rules"]


def test_ema_three_bars_ago_is_three_intervals_not_two():
    trend, entry = buy_fixture()
    result = evaluate_both(trend, entry)["BUY"]
    values = [c["close"] for c in trend]
    assert result["ema20_3ago"] == ema(values, 20)[-4]
    assert result["ema50_3ago"] == ema(values, 50)[-4]
    assert result["ema20_3ago"] != ema(values, 20)[-3]


def test_warmup_waits_for_mature_ema50_at_both_slope_endpoints():
    trend, entry = buy_fixture()
    result = evaluate_both(trend[:52], entry)
    assert all(gate["status"] == "insufficient_data" for gate in result.values())
    assert result["BUY"]["ema50_3ago"] is None
    assert evaluate_both(trend[:53], entry)["BUY"]["status"] != "insufficient_data"
    assert evaluate_both(trend, entry[:20])["SELL"]["status"] == "insufficient_data"
    assert evaluate_both([], [])["BUY"]["rsi"] is None


def test_wilder_rsi_counts_zero_losses_and_zero_gains_in_period():
    prices = [100 + index for index in range(14)] + [112]
    assert rsi_series(prices)[-1] == pytest.approx(100 * 13 / 14)
    assert rsi_series([100] * 15)[-1] == 50
    assert rsi_series(list(range(20)))[-1] == 100
    assert rsi_series(list(range(20, 0, -1)))[-1] == 0


def test_pivot_requires_two_completed_right_bars():
    bars = candles([10, 11, 13, 11, 10])
    assert not confirmed_swings(bars[:-1])[0]
    highs, _ = confirmed_swings(bars)
    assert len(highs) == 1 and highs[0]["index"] == 2
    assert highs[0]["ts"] == bars[2]["ts"]


def test_confirmation_range_compares_with_previous_ten_only():
    trend, entry = buy_fixture()
    previous_average = sum(c["high"] - c["low"] for c in entry[-11:-1]) / 10
    entry[-1]["high"] = entry[-1]["close"] + 2.0
    result = evaluate_both(trend, entry)["BUY"]
    assert result["avg_prev_10_range"] == pytest.approx(previous_average)
    assert "1m_confirmation_range" in result["failed_rules"]


def test_obstacle_uses_distance_and_preserves_different_side_thresholds():
    trend, entry = buy_fixture()
    trend[20]["high"] = 104.4
    result = evaluate_both(trend, entry)["BUY"]
    assert result["nearest_resistance"] == 104.4
    assert result["resistance_distance"] == pytest.approx(.4)
    assert "30s_obstacle_clearance" in result["failed_rules"]  # .4 < 1.5 * .3
    result = evaluate_both(mirror(trend), mirror(entry))["SELL"]
    assert result["support_distance"] == pytest.approx(.4)
    assert result["passed"]  # .4 > existing SELL clearance of 1.0 * .3
    trend[20]["high"] = 104.6
    assert evaluate_both(trend, entry)["BUY"]["passed"]


def test_sell_retains_existing_descending_candle_fallback():
    trend = candles([110 - index * .05 for index in range(60)], 30)
    entry = candles([110 - index * .5 for index in range(40)])
    result = evaluate_both(trend, entry)["SELL"]
    assert not result["entry_swing_highs"] and not result["entry_swing_lows"]
    assert "1m_confirmed_pullback_swings_or_descending_highs" not in result["failed_rules"]
    assert "1m_confirmed_breakout_swing_or_descending_lows" not in result["failed_rules"]
    assert result["failed_rules"] == ["1m_rsi14_band", "1m_rsi14_direction"]


def test_diagnostics_are_complete_even_when_first_rule_fails_and_json_safe():
    trend, entry = buy_fixture()
    original = copy.deepcopy((trend, entry))
    result = evaluate_both(trend, entry)
    sell = result["SELL"]
    assert sell["reason"] == "30s_close_vs_ema20"
    assert sell["ema9"] is not None and sell["rsi"] is not None
    assert len(sell["failed_rules"]) > 1
    assert sell["trend_ts"] == result["BUY"]["trend_ts"]
    assert sell["entry_ts"] == result["BUY"]["entry_ts"]
    json.dumps(result, allow_nan=False)
    json.dumps(evaluate_both([], []), allow_nan=False)
    assert (trend, entry) == original


def test_diagnostics_identify_timeframes_boundaries_and_rule_requirements():
    trend, entry = buy_fixture()
    results = evaluate_both(trend, entry)
    for direction, gate in results.items():
        assert "close" not in gate
        assert gate["trend_close"] == trend[-1]["close"]
        assert gate["entry_close"] == entry[-1]["close"]
        assert gate["trend_timeframe_seconds"] == 30
        assert gate["entry_timeframe_seconds"] == 60
        assert gate["closed_only"] is True
        for prefix, bars, period in (("trend", trend, 30), ("entry", entry, 60)):
            opened = datetime.fromisoformat(gate[f"{prefix}_open_at"])
            closed = datetime.fromisoformat(gate[f"{prefix}_close_at"])
            assert opened.utcoffset().total_seconds() == 0
            assert opened.timestamp() == bars[-1]["ts"]
            assert closed.timestamp() == bars[-1]["ts"] + period
        assert gate["support_resistance_reference"] == "entry_close"
        assert gate["pullback_window_candles"] == len(gate["pullback_closes_ema21"]) == 5
        multiplier = 1.5 if direction == "BUY" else 1.0
        assert gate["required_obstacle_clearance"] == pytest.approx(
            multiplier * (entry[-1]["high"] - entry[-1]["low"]))
        assert gate["required_rsi_band"] == ([50.0, 65.0] if direction == "BUY" else [35.0, 50.0])
        assert gate["failed_rule_count"] == len(gate["failed_rules"])
    assert results["BUY"]["passed"]


def test_unavailable_diagnostics_do_not_invent_boundaries_or_clearance():
    results = evaluate_both([], [])
    for gate in results.values():
        assert gate["trend_close"] is None and gate["entry_close"] is None
        assert gate["trend_open_at"] is None and gate["trend_close_at"] is None
        assert gate["entry_open_at"] is None and gate["entry_close_at"] is None
        assert gate["required_obstacle_clearance"] is None
        assert gate["failed_rule_count"] == 1
        assert gate["failed_rules"] == ["insufficient_closed_candles"]


@pytest.mark.parametrize("bad", ["duplicate_timestamp", "nan", "invalid_range"])
def test_invalid_input_fails_closed(bad):
    trend, entry = buy_fixture()
    if bad == "duplicate_timestamp":
        entry[-1]["ts"] = entry[-2]["ts"]
    elif bad == "nan":
        entry[-1]["close"] = float("nan")
    else:
        entry[-1]["high"] = entry[-1]["low"] - 1
    result = evaluate_both(trend, entry)
    assert all(gate["status"] == "invalid_data" for gate in result.values())
    json.dumps(result, allow_nan=False)
