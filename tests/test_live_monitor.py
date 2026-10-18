"""Forming bars may update diagnostics but cannot manufacture confirmation."""

import copy
import json

import pytest

from strategies.closed_candle import ema, rsi_series
from strategies.live_monitor import analyze_forming


def candles(values, period=30):
    return [dict(open=value, close=value, high=value + .2, low=value - .2, ts=index * period)
            for index, value in enumerate(values)]


def forming(values, period=30):
    closed = candles(values, period)
    start = len(closed) * period
    bar = dict(open=10., high=13., low=9., close=12., ts=start, observed_at=start + 10)
    return closed, bar, start + 10


def test_live_ohlc_wicks_direction_and_remaining_are_distinct_from_expiry():
    closed, bar, now = forming([10] * 60)
    before = copy.deepcopy((closed, bar))
    result = analyze_forming(closed, bar, 30, now)
    assert result["current"] and result["provisional"]
    assert result["can_confirm_entry"] is False
    assert result["candle"]["body"] == 2
    assert result["candle"]["upper_wick"] == result["candle"]["lower_wick"] == 1
    assert result["candle"]["direction"] == "bullish"
    assert result["candle"]["remaining_seconds"] == 20
    assert (closed, bar) == before
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("period,fast,slow", [(30, 20, 50), (60, 9, 21)])
def test_live_indicators_are_recalculated_without_appending_closed_bars(period, fast, slow):
    values = [10 + (index % 3) * .1 for index in range(60)]
    closed, bar, now = forming(values, period)
    initial = analyze_forming(closed, bar, period, now)
    bar.update(close=9.5, observed_at=now + 1)
    updated = analyze_forming(closed, bar, period, now + 1)
    assert updated["indicators"][f"ema{fast}"] == ema(values + [9.5], fast)[-1]
    assert updated["indicators"][f"ema{slow}"] == ema(values + [9.5], slow)[-1]
    assert updated["indicators"]["rsi14"] == rsi_series(values + [9.5])[-1]
    assert initial["indicators"] != updated["indicators"]
    assert len(closed) == 60
    assert updated["candle"]["direction"] == "bearish"
    assert updated["candle"]["remaining_seconds"] == period - 11


def test_30s_entry_role_reports_entry_indicators_without_authorizing_forming_bar():
    values = [10 + (index % 3) * .1 for index in range(60)]
    closed, bar, now = forming(values, 30)
    before = copy.deepcopy((closed, bar))
    trend = analyze_forming(closed, bar, 30, now)
    entry = analyze_forming(closed, bar, 30, now, role="entry")
    assert trend["role"] == "trend" and entry["role"] == "entry"
    assert trend["indicators"]["ema20"] == ema(values + [12.], 20)[-1]
    assert entry["indicators"]["ema9"] == ema(values + [12.], 9)[-1]
    assert entry["indicators"]["ema21"] == ema(values + [12.], 21)[-1]
    assert "ema20" not in entry["indicators"]
    assert "price_above_ema21" in entry["provisional_conditions"]
    assert "range_within_twice_previous_ten" in entry["provisional_conditions"]
    assert "ema20_and_ema50_rising_three_bars" not in entry["provisional_conditions"]
    assert entry["candle"] == trend["candle"]
    assert entry["confirmed_structure"] == trend["confirmed_structure"]
    assert entry["timeframe_seconds"] == 30 and entry["candle"]["remaining_seconds"] == 20
    assert entry["current"] and entry["provisional"] and not entry["can_confirm_entry"]
    assert (closed, bar) == before


@pytest.mark.parametrize("period,role", [(30, "trend"), (60, "entry")])
def test_explicit_default_roles_preserve_monitor_readings(period, role):
    closed, bar, now = forming([10.] * 60, period)
    assert analyze_forming(closed, bar, period, now, role=role) == analyze_forming(closed, bar, period, now)


def test_partial_short_entry_ohlc_cannot_claim_confirmation_range():
    closed, bar, now = forming([10.] * 60, 30)
    bar["partial"] = True
    entry = analyze_forming(closed, bar, 30, now, role="entry")
    assert entry["status"] == "partial" and entry["indicators"]["ema21"] is not None
    assert entry["provisional_conditions"]["range_within_twice_previous_ten"] is None
    assert entry["provisional_conditions"]["low_above_prior_swing_low"] is None
    assert not entry["can_confirm_entry"]


def test_invalid_monitor_role_is_rejected():
    with pytest.raises(ValueError, match="Monitoring role"):
        analyze_forming([], None, 30, 120, role="unsupported")


def test_second_right_bar_is_not_a_confirmed_swing_until_it_closes():
    closed = candles([10, 11, 13, 11])
    bar = dict(candles([10] * 5)[-1], observed_at=130)
    result = analyze_forming(closed, bar, 30, 130)
    assert result["confirmed_structure"]["highs"] == []
    candidate = result["swing_candidates"]["highs"][0]
    assert candidate["ts"] == 60 and candidate["confirmed"] is False
    assert candidate["closed_right_bars"] == 1
    assert candidate["required_more_closed_right_bars"] == 1
    closed.append(bar)
    bar = dict(open=10, high=10.2, low=9.8, close=10, ts=150, observed_at=150)
    result = analyze_forming(closed, bar, 30, 150)
    assert result["confirmed_structure"]["highs"][0]["ts"] == 60


def test_confirmed_hh_hl_lh_ll_and_live_support_distance():
    # Strict pivots at 2/8 (highs) and 5/11 (lows).
    values = [10, 11, 13, 11, 10, 8, 10, 11, 14, 12, 11, 9, 11, 12]
    closed = candles(values)
    bar = dict(open=12, high=13, low=11, close=12, ts=420, observed_at=425)
    result = analyze_forming(closed, bar, 30, 425)
    assert result["confirmed_structure"]["label"] == "HH+HL"
    assert result["support_resistance"]["nearest_resistance"] == 13.2
    assert result["support_resistance"]["nearest_support"] == 8.8
    bar.update(close=12.5, observed_at=426)
    updated = analyze_forming(closed, bar, 30, 426)
    assert updated["support_resistance"]["resistance_distance"] == pytest.approx(.7)
    mirrored = [dict(open=30-b["open"], high=30-b["low"], low=30-b["high"],
                     close=30-b["close"], ts=b["ts"]) for b in closed]
    bar = dict(open=18, high=19, low=17, close=18, ts=420, observed_at=425)
    result = analyze_forming(mirrored, bar, 30, 425)
    assert result["confirmed_structure"]["label"] == "LH+LL"


@pytest.mark.parametrize("change,offset,reason", [
    ({}, 3, "stale_quote"), ({"observed_at": 1811}, 0, "future_quote"),
    ({"observed_at": 1830}, 20, "forming_candle_not_current"),
    ({"observed_at": None}, 0, "quote_timestamp_unavailable"),
])
def test_old_or_unverifiable_prices_are_not_reported_as_current(change, offset, reason):
    closed, bar, now = forming([10] * 60)
    bar.update(change)
    result = analyze_forming(closed, bar, 30, now + offset)
    assert result["current"] is False
    assert result["live_price"] is None and result["candle"] is None
    assert result["indicators"] == {} and result["provisional_conditions"] == {}
    assert result["reason"] == reason
    json.dumps(result, allow_nan=False)


def test_partial_ohlc_is_flagged_and_cannot_claim_a_valid_confirmation_range():
    closed, bar, now = forming([10] * 60, 60)
    bar["partial"] = True
    result = analyze_forming(closed, bar, 60, now)
    assert result["status"] == "partial" and result["candle"]["partial"]
    assert result["indicators"]["ema21"] is not None
    assert result["provisional_conditions"]["range_within_twice_previous_ten"] is None
    assert result["can_confirm_entry"] is False


def test_incomplete_warmup_and_missing_forming_data_do_not_invent_values():
    closed, bar, now = forming([10, 11])
    result = analyze_forming(closed, bar, 30, now)
    assert result["indicators"]["ema20"] is None
    assert result["indicators"]["rsi14"] is None
    assert analyze_forming(closed, None, 30, now)["reason"] == "forming_candle_unavailable"


def test_gaps_and_invalid_candles_fail_closed():
    closed, bar, now = forming([10] * 60)
    gap = analyze_forming(closed[:-1], bar, 30, now)
    assert gap["reason"] == "closed_history_gap" and gap["current"]
    assert gap["candle"]["close"] == bar["close"]
    assert gap["candle"]["body"] == 2 and gap["live_price"] == 12
    assert not gap["indicators"] and not gap["confirmed_structure"]
    assert not gap["swing_candidates"] and not gap["provisional_conditions"]
    assert not gap["support_resistance"]
    assert gap["can_confirm_entry"] is False
    # A hole inside history also preserves the independently fresh candle.
    inside_gap = analyze_forming(closed[:20] + closed[21:], bar, 30, now)
    assert inside_gap["status"] == "history_gap" and inside_gap["current"]
    bar["high"] = float("nan")
    result = analyze_forming(closed, bar, 30, now)
    assert result["status"] == "invalid" and not result["current"]
    json.dumps(result, allow_nan=False)


def test_source_stale_flag_wins_over_recent_observation_timestamp():
    closed, bar, now = forming([10] * 60)
    bar["stale"] = True
    result = analyze_forming(closed, bar, 30, now)
    assert result["status"] == "stale" and result["reason"] == "stale_quote"
    assert not result["current"] and result["live_price"] is None
    assert result["candle"] is None and not result["indicators"]


def test_unaligned_history_timestamps_are_invalid_even_when_ordered():
    closed, bar, now = forming([10] * 60)
    closed[20]["ts"] += 1
    result = analyze_forming(closed, bar, 30, now)
    assert result["status"] == "invalid"
    assert result["reason"] == "unaligned_history_timestamps"
    assert not result["current"] and not result["indicators"]


@pytest.mark.parametrize("period", [30, 60])
def test_fresh_unchanged_price_at_boundary_requires_current_bucket_sample(period):
    closed, bar, _ = forming([10] * 60, period)
    start = bar["ts"]
    bar.update(observed_at=start - .5, sampled_at=start + .25)
    result = analyze_forming(closed, bar, period, start + .5)
    assert result["current"] and result["quote_age_seconds"] == 1
    assert result["candle"]["observed_at"] == start - .5
    assert result["candle"]["sampled_at"] == start + .25
    assert result["candle"]["remaining_seconds"] == period - .5
    bar.pop("sampled_at")
    result = analyze_forming(closed, bar, period, start + .5)
    assert not result["current"] and result["reason"] == "forming_candle_not_current"
    bar["sampled_at"] = start + .25
    result = analyze_forming(closed, bar, period, start + 2)
    assert not result["current"] and result["reason"] == "stale_quote"
