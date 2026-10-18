import json

from scripts.analyze_monitoring_log import summarize


def test_report_counts_unique_setups_and_actual_order_ids_without_forcing_frequency(tmp_path):
    path = tmp_path / "monitor.log"
    records = []
    for timestamp in (121, 122, 123, 127):
        records.append("[Decision] " + json.dumps({
            "evaluated_at": timestamp, "asset": "EUR/USD OTC", "data_status": "fresh",
            "reason": "order_unresolved", "confirmed_buy": {"passed": True, "entry_ts": 60},
            "trend_filter_5m": {"passed": True},
        }))
    # A later passing setup observed outside its entry window cannot be used.
    records.append("[Decision] " + json.dumps({
        "evaluated_at": 190, "asset": "EUR/USD OTC", "data_status": "fresh",
        "confirmed_buy": {"passed": True, "entry_ts": 120},
        "trend_filter_5m": {"passed": True},
    }))
    records.extend(['[Execution] {"status":"accepted", "id":"demo-1"}'] * 2)
    path.write_text("\n".join(records))
    result = summarize(path)
    assert result["unique_confirmed_buy_setups"] == 2
    assert result["fresh_setups_in_entry_window"] == 1
    assert result["fresh_setups_in_five_second_entry_window"] == 1
    assert result["setups_also_passing_logged_5m_filter"] == 1
    assert result["observed_accepted_order_ids"] == 1
    assert result["frequency_target_verified"] is False
    assert result["observed_seconds"] == 69
    assert result["decision_reasons"]["order_unresolved"] == 4
    assert result["decision_reasons_per_unique_entry"]["order_unresolved"] == 1
    assert result["rejection_stages_per_unique_entry"] == {"execution": 1, "outside_entry_window": 1}


def _write_decisions(path, records):
    path.write_text("\n".join("[Decision] " + json.dumps(record) for record in records))


def test_report_prefers_buy_evaluation_and_recognizes_a_later_qualifying_setup(tmp_path):
    path = tmp_path / "monitor.log"
    common = {
        "asset": "EUR/USD OTC", "data_status": "fresh", "decision": "WAIT",
        "trend_filter_5m": {"passed": True},
        # The current field must take precedence when both schemas are present.
        "confirmed_buy": {"passed": False, "entry_ts": 60},
    }
    _write_decisions(path, [
        dict(common, evaluated_at=121, blocking_stage="entry_rules", reason="1m_rsi14_band",
             buy_evaluation={"passed": False, "entry_ts": 60, "failed_rules": ["1m_rsi14_band"]}),
        dict(common, evaluated_at=122, blocking_stage="execution", reason="order_unresolved",
             buy_evaluation={"passed": True, "entry_ts": 60, "failed_rules": []}),
    ])
    result = summarize(path)
    assert result["unique_confirmed_buy_setups"] == 1
    assert result["fresh_setups_in_five_second_entry_window"] == 1
    assert result["evaluated_entry_candles"] == 1
    assert result["rejection_stages_per_unique_entry"] == {"execution": 1}
    assert result["failed_rules_per_unique_setup"] == {"1m_rsi14_band": 1}
    assert "1 qualifying closed-candle BUY setup(s)" in result["no_trade_explanation"]


def test_report_distinguishes_rejections_without_counting_every_poll(tmp_path):
    path = tmp_path / "monitor.log"
    stages = [
        ("entry_rules", "1m_close_vs_ema21", False),
        ("data", "stale_candle_snapshot", True),
        ("timing", "awaiting_next_closed_confirmation", True),
        ("trend_filter", "5m_neutral", True),
        ("risk", "capacity", True),
        ("execution", "order_unresolved", True),
    ]
    records = []
    for index, (stage, reason, passed) in enumerate(stages, 1):
        entry_ts = index * 60
        for second in (1, 2, 3):
            records.append({
                "asset": "EUR/USD OTC", "evaluated_at": entry_ts + 60 + second,
                "decision": "WAIT", "data_status": "fresh", "blocking_stage": stage,
                "reason": reason, "buy_evaluation": {"passed": passed, "entry_ts": entry_ts},
                "trend_filter_5m": {"passed": stage != "trend_filter"},
            })
    _write_decisions(path, records)
    result = summarize(path)
    assert result["decision_records"] == 18
    assert result["evaluated_entry_candles"] == 6
    assert result["rejection_stages_per_unique_entry"] == {
        "no_qualifying_setup": 1, "stale_data": 1, "outside_entry_window": 1,
        "five_minute_filter": 1, "risk": 1, "execution": 1,
    }
    assert result["rejection_reasons_per_unique_entry"] == {reason: 1 for _, reason, _ in stages}


def test_report_checks_closed_history_freshness_and_configured_entry_window(tmp_path):
    path = tmp_path / "monitor.log"
    _write_decisions(path, [{
        "evaluated_at": 121, "asset": "EUR/USD OTC", "data_status": "fresh",
        "quote_status": "fresh", "closed_candle_status": "stale_or_unavailable",
        "buy_evaluation": {"passed": True, "entry_ts": 60},
        "trend_filter_5m": {"passed": True},
    }, {
        "evaluated_at": 187, "asset": "EUR/USD OTC", "data_status": "fresh",
        "quote_status": "fresh", "closed_candle_status": "fresh",
        "confirmation_window_seconds": 10, "decision": "BUY",
        "buy_evaluation": {"passed": True, "entry_ts": 120},
        "trend_filter_5m": {"passed": True},
    }])
    result = summarize(path)
    assert result["unique_confirmed_buy_setups"] == 2
    assert result["fresh_setups_in_entry_window"] == 1
    assert result["fresh_setups_in_five_second_entry_window"] == 0
    assert result["setups_also_passing_logged_5m_filter"] == 1
    assert result["rejection_stages_per_unique_entry"] == {"stale_data": 1}


def test_report_explains_absent_setups_and_deduplicates_failures(tmp_path):
    path = tmp_path / "monitor.log"
    _write_decisions(path, [{
        "evaluated_at": second, "asset": "EUR/USD OTC", "data_status": "fresh",
        "reason": "1m_close_vs_ema21", "decision": "WAIT", "blocking_stage": "entry_rules",
        "buy_evaluation": {"passed": False, "entry_ts": 60,
                           "failed_rules": ["1m_close_vs_ema21", "1m_rsi14_band"]},
    } for second in range(121, 130)])
    result = summarize(path)
    assert result["no_trade_explanation"].startswith("No qualifying closed-candle BUY setup was observed.")
    assert result["rejection_stages_per_unique_entry"] == {"no_qualifying_setup": 1}
    assert result["failed_rules_per_unique_setup"] == {"1m_close_vs_ema21": 1, "1m_rsi14_band": 1}


def test_report_deduplicates_missing_history_and_ignores_non_object_payloads(tmp_path):
    path = tmp_path / "monitor.log"
    _write_decisions(path, [{
        "evaluated_at": second, "asset": "EUR/USD OTC", "data_status": "stale_or_unavailable",
        "blocking_stage": "data", "reason": "stale_candle_snapshot", "buy_evaluation": {},
    } for second in range(121, 124)])
    with path.open("a") as stream:
        stream.write('\n[Decision] []\n[Decision] invalid-json')
    result = summarize(path)
    assert result["decision_records"] == 3
    assert result["evaluated_entry_candles"] == 0
    assert result["rejection_stages_per_unique_entry"] == {"stale_data": 1}
    assert result["no_trade_explanation"].startswith("Trading evaluations were blocked by stale or unavailable data")


def test_report_uses_thirty_second_close_for_entry_window(tmp_path):
    path = tmp_path / "monitor.log"
    _write_decisions(path, [{
        "evaluated_at": evaluated_at, "asset": "EUR/USD OTC", "data_status": "fresh",
        "entry_timeframe_seconds": 30,
        "buy_evaluation": {"passed": True, "entry_ts": entry_ts},
        "trend_filter_5m": {"passed": True},
    } for entry_ts, evaluated_at in ((60, 90), (60, 95), (90, 126), (120, 149))])
    result = summarize(path)
    assert result["unique_confirmed_buy_setups"] == 3
    assert result["fresh_setups_in_entry_window"] == 1
    assert result["fresh_setups_in_five_second_entry_window"] == 1
    assert result["setups_also_passing_logged_5m_filter"] == 1
    assert result["rejection_stages_per_unique_entry"] == {"outside_entry_window": 2}


def test_report_accepts_period_from_buy_evaluation_and_configured_window(tmp_path):
    path = tmp_path / "monitor.log"
    _write_decisions(path, [{
        "evaluated_at": 97, "asset": "EUR/USD OTC", "data_status": "fresh",
        "confirmation_window_seconds": 10,
        "buy_evaluation": {"passed": True, "entry_ts": 60, "entry_timeframe_seconds": 30},
        "trend_filter_5m": {"passed": True},
    }])
    result = summarize(path)
    assert result["fresh_setups_in_entry_window"] == 1
    assert result["fresh_setups_in_five_second_entry_window"] == 0
    assert result["setups_also_passing_logged_5m_filter"] == 1
    assert result["rejection_stages_per_unique_entry"] == {}


def test_report_keeps_colliding_periods_distinct_and_deduplicates_legacy_minutes(tmp_path):
    path = tmp_path / "monitor.log"
    common = {
        "asset": "EUR/USD OTC", "data_status": "fresh",
        "trend_filter_5m": {"passed": True},
        "reason": "order_unresolved", "blocking_stage": "execution",
        "buy_evaluation": {"passed": True, "entry_ts": 60},
    }
    _write_decisions(path, [
        dict(common, evaluated_at=91, entry_timeframe_seconds=30),
        dict(common, evaluated_at=92, entry_timeframe_seconds=30),
        dict(common, evaluated_at=121, entry_timeframe_seconds=60),
        dict(common, evaluated_at=122),
    ])
    result = summarize(path)
    assert result["evaluated_entry_candles"] == 2
    assert result["unique_confirmed_buy_setups"] == 2
    assert result["fresh_setups_in_entry_window"] == 2
    assert result["fresh_setups_in_five_second_entry_window"] == 2
    assert result["setups_also_passing_logged_5m_filter"] == 2
    assert result["rejection_stages_per_unique_entry"] == {"execution": 2}
    assert result["decision_reasons_per_unique_entry"] == {"order_unresolved": 2}


def test_report_does_not_hide_failed_minute_setup_when_thirty_second_setup_passes(tmp_path):
    path = tmp_path / "monitor.log"
    common = {"asset": "EUR/USD OTC", "data_status": "fresh", "trend_filter_5m": {"passed": True}}
    _write_decisions(path, [
        dict(common, evaluated_at=91, entry_timeframe_seconds=30,
             buy_evaluation={"passed": True, "entry_ts": 60}),
        dict(common, evaluated_at=121, entry_timeframe_seconds=60,
             blocking_stage="entry_rules", reason="1m_rsi14_band",
             buy_evaluation={"passed": False, "entry_ts": 60, "failed_rules": ["1m_rsi14_band"]}),
    ])
    result = summarize(path)
    assert result["evaluated_entry_candles"] == 2
    assert result["unique_confirmed_buy_setups"] == 1
    assert result["rejection_stages_per_unique_entry"] == {"no_qualifying_setup": 1}
    assert result["failed_rules_per_unique_setup"] == {"1m_rsi14_band": 1}


def _directional_record(direction, evaluated_at=91, entry_ts=60, passed=True, **overrides):
    selected = {"direction": direction, "passed": passed, "entry_ts": entry_ts,
                "entry_timeframe_seconds": 30,
                "failed_rules": [] if passed else ["30s_close_breaks_swing"]}
    opposite = {"direction": "SELL" if direction == "BUY" else "BUY", "passed": False,
                "entry_ts": entry_ts, "entry_timeframe_seconds": 30,
                "failed_rules": ["opposite_direction_rule"]}
    return {
        "asset": "EUR/USD OTC", "evaluated_at": evaluated_at, "data_status": "fresh",
        "direction": direction if passed else None, "decision": "WAIT",
        "reason": f"confirmed_{direction.lower()}_observe_only" if passed else "30s_close_breaks_swing",
        "blocking_stage": "confirmation" if passed else "entry_rules",
        "buy_evaluation": selected if direction == "BUY" else opposite,
        "sell_evaluation": selected if direction == "SELL" else opposite,
        "entry_evaluation": selected,
        "trend_filter_5m": {"passed": True, "required_direction": direction,
                            "direction": "bullish" if direction == "BUY" else "bearish"},
        **overrides,
    }


def test_report_counts_both_directions_without_relabeling_sell_as_buy(tmp_path):
    path = tmp_path / "monitor.log"
    # A direction change on one timestamp stays distinct, even across repeated polls.
    _write_decisions(path, [
        _directional_record("BUY", evaluated_at=91),
        _directional_record("BUY", evaluated_at=92),
        _directional_record("SELL", evaluated_at=93),
        _directional_record("SELL", evaluated_at=94),
    ])
    result = summarize(path)
    assert result["evaluated_entry_candles"] == 1
    assert result["unique_confirmed_buy_setups"] == 1
    assert result["unique_confirmed_sell_setups"] == 1
    assert result["unique_confirmed_setups"] == 2
    assert result["fresh_setups_in_entry_window"] == 1
    assert result["fresh_setups_in_five_second_entry_window"] == 1
    assert result["setups_also_passing_logged_5m_filter"] == 1
    assert result["total_fresh_setups_in_entry_window"] == 2
    assert result["total_fresh_setups_in_five_second_entry_window"] == 2
    assert result["total_setups_also_passing_logged_5m_filter"] == 2
    assert result["setup_counts_by_direction"] == {
        direction: {"unique_confirmed_setups": 1, "fresh_setups_in_entry_window": 1,
                    "fresh_setups_in_five_second_entry_window": 1,
                    "setups_also_passing_logged_5m_filter": 1, "evaluated_entry_candles": 1}
        for direction in ("BUY", "SELL")
    }
    assert result["rejection_stages_per_unique_entry"] == {}
    assert result["failed_rules_per_unique_setup"] == {}
    assert "2 qualifying closed-candle BUY/SELL setup(s)" in result["no_trade_explanation"]


def test_report_uses_selected_sell_failures_and_preserves_opposite_rejection(tmp_path):
    path = tmp_path / "monitor.log"
    _write_decisions(path, [
        _directional_record("BUY", evaluated_at=91),
        _directional_record("SELL", evaluated_at=92, passed=False),
        _directional_record("SELL", evaluated_at=93, passed=False),
    ])
    result = summarize(path)
    assert result["unique_confirmed_buy_setups"] == 1
    assert result["unique_confirmed_sell_setups"] == 0
    assert result["rejection_stages_per_unique_entry"] == {"no_qualifying_setup": 1}
    assert result["failed_rules_per_unique_setup"] == {"30s_close_breaks_swing": 1}
    assert result["failed_rules_by_direction"] == {"BUY": {}, "SELL": {"30s_close_breaks_swing": 1}}


def test_report_sell_setups_respect_data_timing_risk_and_filter_direction(tmp_path):
    path = tmp_path / "monitor.log"
    _write_decisions(path, [
        _directional_record("SELL", evaluated_at=91, blocking_stage="data", reason="stale_candle_snapshot",
                            closed_candle_status="stale_or_unavailable"),
        _directional_record("SELL", entry_ts=90, evaluated_at=126,
                            blocking_stage="timing", reason="awaiting_next_closed_confirmation"),
        _directional_record("SELL", entry_ts=120, evaluated_at=151,
                            blocking_stage="risk", reason="capacity"),
        _directional_record("SELL", entry_ts=150, evaluated_at=181,
                            blocking_stage="trend_filter", reason="5m_bullish",
                            trend_filter_5m={"passed": False, "required_direction": "SELL",
                                             "direction": "bullish"}),
    ])
    result = summarize(path)
    assert result["unique_confirmed_buy_setups"] == 0
    assert result["unique_confirmed_sell_setups"] == 4
    assert result["fresh_setups_in_entry_window"] == 0
    assert result["total_fresh_setups_in_entry_window"] == 2
    assert result["total_setups_also_passing_logged_5m_filter"] == 1
    assert result["rejection_stages_per_unique_entry"] == {
        "stale_data": 1, "outside_entry_window": 1, "risk": 1, "five_minute_filter": 1,
    }
    assert result["failed_rules_per_unique_setup"] == {}


def test_report_never_applies_bullish_filter_to_sell_setup(tmp_path):
    path = tmp_path / "monitor.log"
    _write_decisions(path, [
        _directional_record("SELL", trend_filter_5m={"passed": True, "required_direction": "BUY"}),
    ])
    result = summarize(path)
    assert result["unique_confirmed_sell_setups"] == 1
    assert result["total_fresh_setups_in_entry_window"] == 1
    assert result["total_setups_also_passing_logged_5m_filter"] == 0


def test_report_supports_entry_evaluation_without_duplicate_side_fields(tmp_path):
    path = tmp_path / "monitor.log"
    record = _directional_record("SELL", decision="SELL", reason="closed_sell_and_5m_filter_confirmed")
    del record["buy_evaluation"]
    del record["sell_evaluation"]
    _write_decisions(path, [record])
    result = summarize(path)
    assert result["unique_confirmed_buy_setups"] == 0
    assert result["unique_confirmed_sell_setups"] == 1
    assert result["total_fresh_setups_in_entry_window"] == 1
    assert result["rejection_stages_per_unique_entry"] == {}
