from datetime import datetime, timedelta, timezone

from bot.campaign import CampaignTracker
from bot.live_feed import candle_momentum
from strategies.ema_pattern import evaluate_one_indicator
from bot.trade_governor import TradeGovernor


def test_campaign_verdict_excludes_draws_from_win_rate_and_decided(tmp_path):
    journal = tmp_path / "trade_journal.csv"
    journal.write_text(
        "timestamp,result,pnl,state_key\n"
        "2024-01-01T00:00:00Z,WIN,100,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:01:00Z,WIN,75,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:02:00Z,WIN,80,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:03:00Z,WIN,60,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:04:00Z,WIN,40,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:05:00Z,WIN,95,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:06:00Z,WIN,70,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:07:00Z,WIN,85,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:08:00Z,WIN,90,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:09:00Z,WIN,110,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:10:00Z,WIN,105,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:11:00Z,WIN,55,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:12:00Z,WIN,50,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:13:00Z,WIN,60,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:14:00Z,WIN,65,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:15:00Z,LOSS,-120,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:16:00Z,LOSS,-110,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:17:00Z,LOSS,-90,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:18:00Z,LOSS,-80,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:19:00Z,LOSS,-100,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:20:00Z,LOSS,-90,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:21:00Z,LOSS,-95,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:22:00Z,LOSS,-85,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:23:00Z,LOSS,-80,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:24:00Z,DRAW,0,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:25:00Z,DRAW,0,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:26:00Z,DRAW,0,ONE_INDICATOR_EMA\n",
        encoding="utf-8",
    )
    tracker = CampaignTracker(str(journal), str(tmp_path), state_key="ONE_INDICATOR_EMA", target=1000, payout=0.85)
    stats = tracker.stats()
    assert stats["wins"] == 15
    assert stats["losses"] == 9
    assert stats["draws"] == 3
    assert stats["decided"] == 24
    assert stats["win_rate"] == 0.625
    assert stats["verdict"] == "NOT PROVEN"
    assert stats["wilson_lower"] < stats["break_even"]


def test_campaign_start_filters_rows_and_logs_breakdown(tmp_path):
    journal = tmp_path / "trade_journal.csv"
    journal.write_text(
        "timestamp,result,pnl,state_key\n"
        "2024-01-01T00:00:00Z,WIN,100,ONE_INDICATOR_EMA\n"
        "2024-01-01T12:00:00Z,LOSS,-100,ONE_INDICATOR_EMA\n"
        "2024-01-02T00:00:00Z,WIN,200,ONE_INDICATOR_EMA\n",
        encoding="utf-8",
    )
    start_path = tmp_path / "campaign_start.json"
    start_path.write_text('{"start": "2024-01-02T00:00:00Z"}', encoding="utf-8")
    tracker = CampaignTracker(str(journal), str(tmp_path), state_key="ONE_INDICATOR_EMA", target=1000, payout=0.85)
    tracker.start_time = datetime.fromisoformat("2024-01-02T00:00:00+00:00").timestamp()
    stats = tracker.stats()
    assert stats["trades"] == 1
    assert stats["wins"] == 1
    assert stats["excluded_before_start"] == 2


def test_candle_momentum_doji_is_flat_and_not_synthesized():
    candle1 = {"open": 1.08515, "high": 1.08526, "low": 1.08510, "close": 1.08515, "ticks": 12, "up_ticks": 7, "down_ticks": 5}
    candle2 = {"open": 1.08514, "high": 1.08523, "low": 1.08511, "close": 1.08514, "ticks": 12, "up_ticks": 5, "down_ticks": 7}
    assert candle_momentum(candle1)["body"] == 0.0
    assert candle_momentum(candle1)["range"] == 0.00016
    assert candle_momentum(candle1)["body_ratio"] == 0.0
    assert candle_momentum(candle1)["direction"] == "flat"
    assert candle_momentum(candle2)["body"] == 0.0
    assert candle_momentum(candle2)["range"] == 0.00012
    assert candle_momentum(candle2)["direction"] == "flat"


def test_pattern_blocked_signal_keeps_ema_strength_and_signal():
    bars = [{"close": 1.0850}, {"close": 1.08515}]
    result = evaluate_one_indicator(bars, period_seconds=30)
    assert result.get("signal") == "BUY"
    assert result.get("strength_bp") is not None


def test_trade_governor_window_keeps_history_bounded():
    governor = TradeGovernor(max_per_hour=100, min_spacing=1.0, percentile=90.0, window=300, min_history=1, max_per_day=1000, state_path=None, clock=lambda: 1000.0)
    governor.seed([float(i) for i in range(500)])
    for i in range(200):
        governor.observe(float(1000 + i))
    assert len(governor.strengths) <= 300
    assert governor.threshold() is not None
