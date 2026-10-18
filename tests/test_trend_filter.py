"""The five-minute filter measures direction without contrarian entry scoring."""

import copy
import json

import pytest

from strategies.closed_candle import ema
from strategies.trend_filter import evaluate_five_minute_trend


def history(count=35, direction=1):
    return [dict(open=1.1 + direction * index * .0001,
                 high=1.1 + direction * index * .0001 + .00003,
                 low=1.1 + direction * index * .0001 - .00003,
                 close=1.1 + direction * index * .0001,
                 ts=index * 300, source="closed_test_fixture")
            for index in range(count)]


def evaluate(bars, direction="BUY"):
    return evaluate_five_minute_trend(bars, bars[-1]["ts"] + 301, direction)


def test_rising_completed_candles_pass_even_when_rsi_would_be_overbought():
    bars = history()
    before = copy.deepcopy(bars)
    result = evaluate(bars)
    assert result["passed"] and result["vote"] == "BUY"
    assert result["direction"] == "bullish" and result["reason"] == "5m_bullish"
    assert result["failed_rules"] == []
    assert result["close"] > result["ema21"]
    assert result["ema9"] > result["ema21"]
    assert result["ema9_slope_3_candles"] > 0 and result["ema21_slope_3_candles"] > 0
    assert result["source"] == "closed_test_fixture" and result["closed_only"]
    assert "confidence" not in result
    assert bars == before
    json.dumps(result, allow_nan=False)


def test_falling_completed_candles_are_explicitly_bearish_and_cannot_allow_buy():
    result = evaluate(history(direction=-1))
    assert not result["passed"] and result["vote"] == "SELL"
    assert result["direction"] == "bearish" and result["reason"] == "5m_bearish"
    assert len(result["failed_rules"]) == 4


@pytest.mark.parametrize("market_sign", [1, -1])
def test_default_filter_keeps_legacy_buy_behavior(market_sign):
    bars = history(direction=market_sign)
    now = bars[-1]["ts"] + 301
    assert evaluate_five_minute_trend(bars, now) == evaluate_five_minute_trend(bars, now, "BUY")


@pytest.mark.parametrize("market_sign,market_direction,vote", [
    (1, "bullish", "BUY"), (-1, "bearish", "SELL"),
])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_completed_filter_passes_only_the_requested_trend_side(
        market_sign, market_direction, vote, direction):
    bars = history(direction=market_sign)
    before = copy.deepcopy(bars)
    result = evaluate(bars, direction)
    aligned = direction == vote
    assert result["passed"] is aligned
    assert result["status"] == ("pass" if aligned else "fail")
    assert result["required_direction"] == direction
    assert result["direction"] == market_direction and result["vote"] == vote
    assert result["reason"] == f"5m_{market_direction}"
    assert result["failed_rules"] == ([] if aligned else [
        "5m_close_vs_ema21", "5m_ema9_vs_ema21",
        "5m_ema9_slope_3_candles", "5m_ema21_slope_3_candles",
    ])
    assert bars == before
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_sideways_candles_are_neutral(direction):
    bars = history(direction=0)
    for candle in bars:
        candle.update(open=1., close=1., high=1.01, low=.99)
    result = evaluate(bars, direction)
    assert not result["passed"] and result["vote"] == "NEUTRAL"
    assert result["direction"] == "neutral" and result["reason"] == "5m_neutral"
    assert result["ema9_slope_3_candles"] == result["ema21_slope_3_candles"] == 0
    assert len(result["failed_rules"]) == 4


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_mixed_trend_cannot_confirm_either_side(direction):
    bars = history()
    bars[-1].update(open=1.101, close=1.101, high=1.10103, low=1.10097)
    result = evaluate(bars, direction)
    assert not result["passed"]
    assert result["vote"] == "NEUTRAL" and result["direction"] == "neutral"
    assert 0 < len(result["failed_rules"]) < 4


def test_slopes_use_three_complete_intervals():
    bars = history(count=24)
    result = evaluate(bars)
    closes = [candle["close"] for candle in bars]
    assert result["passed"] and result["count"] == result["required"] == 24
    assert result["ema9_3ago"] == ema(closes, 9)[-4]
    assert result["ema21_3ago"] == ema(closes, 21)[-4]
    assert result["ema21_3ago"] != ema(closes, 21)[-3]


@pytest.mark.parametrize("count", [0, 3, 14, 21, 23])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_immature_slopes_wait_for_sufficient_closed_history(count, direction):
    bars = history(count=count)
    result = evaluate_five_minute_trend(bars, count * 300 + 1, direction)
    assert not result["passed"] and result["vote"] == "HOLD"
    assert result["reason"] == "5m_warmup"
    assert result["ema9"] is None and result["ema21_3ago"] is None


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_forming_price_spike_cannot_confirm_an_opposing_completed_trend(direction):
    bars = history(direction=-1 if direction == "BUY" else 1)
    now = bars[-1]["ts"] + 301
    assert not evaluate_five_minute_trend(bars, now, direction)["passed"]
    spike = 50. if direction == "BUY" else .01
    bars.append(dict(open=1.1, high=max(1.1, spike), low=min(1.1, spike), close=spike, ts=10500))
    result = evaluate_five_minute_trend(bars, now, direction)
    assert result["reason"] == "5m_forming_candle"
    assert not result["passed"] and result["ema9"] is None


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_explicit_unclosed_flag_is_rejected_even_with_an_old_timestamp(direction):
    bars = history()
    bars[-1]["closed"] = False
    assert evaluate(bars, direction)["reason"] == "5m_forming_candle"


@pytest.mark.parametrize("bad,reason", [
    ("stale", "stale_5m_candles"), ("gap", "5m_history_gap"),
    ("unaligned", "5m_unaligned_candles"), ("nan", "invalid_5m_candles"),
    ("infinite_timestamp", "invalid_5m_candles"), ("bad_range", "invalid_5m_candles"),
    ("duplicate_timestamp", "invalid_5m_candles"),
])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_bad_history_fails_closed_without_fabricated_indicators(bad, reason, direction):
    bars = history()
    now = bars[-1]["ts"] + 301
    if bad == "stale":
        now += 300
    elif bad == "gap":
        bars.pop(10)
    elif bad == "unaligned":
        bars[10]["ts"] += 1
    elif bad == "nan":
        bars[-1]["close"] = float("nan")
    elif bad == "infinite_timestamp":
        bars[-1]["ts"] = float("inf")
    elif bad == "bad_range":
        bars[-1]["high"] = bars[-1]["low"] - 1
    else:
        bars[-1]["ts"] = bars[-2]["ts"]
    result = evaluate_five_minute_trend(bars, now, direction)
    assert not result["passed"] and result["reason"] == reason
    assert result["failed_rules"] == [reason]
    assert result["ema9"] is None and result["ema21"] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_completed_filter_does_not_repaint_within_five_minute_bucket(direction):
    bars = history()
    close_time = bars[-1]["ts"] + 300
    assert (evaluate_five_minute_trend(bars, close_time, direction)
            == evaluate_five_minute_trend(bars, close_time + 299, direction))
    assert evaluate_five_minute_trend(bars, close_time + 300, direction)["reason"] == "stale_5m_candles"


@pytest.mark.parametrize("direction", ["HOLD", "buy", "sell", "", None])
def test_unknown_trade_direction_is_rejected(direction):
    with pytest.raises(ValueError, match="direction must be BUY or SELL"):
        evaluate(history(), direction)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("now", [None, "invalid", float("nan"), float("inf")])
def test_invalid_evaluation_time_fails_closed_for_both_sides(direction, now):
    result = evaluate_five_minute_trend(history(), now, direction)
    assert not result["passed"]
    assert result["required_direction"] == direction
    assert result["reason"] == "invalid_evaluation_time"
    assert result["failed_rules"] == ["invalid_evaluation_time"]
