"""Summarize observed BUY/SELL opportunities without projecting guaranteed trades."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics


def _buy_evaluation(record):
    """Read current diagnostics without losing support for archived runs."""
    gate = record.get("buy_evaluation", record.get("confirmed_buy", {}))
    return gate if isinstance(gate, dict) else {}


def _evaluations(record):
    gates = {"BUY": _buy_evaluation(record)}
    sell = record.get("sell_evaluation")
    if isinstance(sell, dict):
        gates["SELL"] = sell
    selected = record.get("entry_evaluation")
    if isinstance(selected, dict) and selected.get("direction") in ("BUY", "SELL"):
        if not gates.get(selected["direction"]):
            gates[selected["direction"]] = selected
    return gates


def _selected_evaluation(record):
    """Use the runtime's selected side for blockers, including failed setups."""
    selected = record.get("entry_evaluation")
    if isinstance(selected, dict) and selected.get("direction") in ("BUY", "SELL"):
        return selected["direction"], selected
    direction = record.get("direction") or record.get("decision")
    if direction not in ("BUY", "SELL"):
        direction = record.get("trend_filter_5m", {}).get("required_direction", "BUY")
    if direction not in ("BUY", "SELL"):
        direction = "BUY"
    return direction, _evaluations(record).get(direction, {})


def _fresh_data(record):
    # Historical data_status covered quotes only. New logs also expose history
    # freshness, which must not be mistaken for a usable closed-candle setup.
    return (record.get("data_status") == "fresh"
            and record.get("quote_status", "fresh") == "fresh"
            and record.get("closed_candle_status", "fresh") == "fresh")


def _entry_period(record, gate=None):
    """Archived logs predate the configurable period and use one-minute entries."""
    if gate is None:
        _, gate = _selected_evaluation(record)
    period = record.get("entry_timeframe_seconds", gate.get("entry_timeframe_seconds", 60))
    return int(period) if period in (30, 60) else 60


def _within_entry_window(record, entry_ts, gate=None):
    return 0 <= record["evaluated_at"] - (entry_ts + _entry_period(record, gate)) <= record.get("confirmation_window_seconds", 5)


def _rejection_stage(record, gate):
    """Prefer the runtime's actual blocking stage; infer it for older logs."""
    reason = record.get("reason", "unknown")
    if record.get("decision") in {"BUY", "SELL"} or reason in {
        "checking_demo_order_conditions", "confirmed_buy_observe_only",
        "closed_buy_and_5m_filter_confirmed", "confirmed_sell_observe_only",
        "closed_sell_and_5m_filter_confirmed",
    }:
        return None
    stage = {
        "data": "stale_data", "entry_rules": "no_qualifying_setup",
        "timing": "outside_entry_window", "trend_filter": "five_minute_filter",
        "risk": "risk", "execution": "execution",
    }.get(record.get("blocking_stage"))
    if stage:
        return stage
    if reason in {"stale_or_unavailable_quote", "stale_candle_snapshot", "quote_expired_during_preflight"} or not _fresh_data(record):
        return "stale_data"
    if reason in {"risk_limit_or_loss_halt", "capacity"}:
        return "risk"
    if reason in {"order_unresolved", "setup_already_attempted", "payout_unavailable_or_below_minimum"}:
        return "execution"
    if gate.get("passed") is False:
        return "no_qualifying_setup"
    if gate.get("passed"):
        entry_ts = gate.get("entry_ts")
        if entry_ts is not None and not _within_entry_window(record, entry_ts, gate):
            return "outside_entry_window"
        if not record.get("trend_filter_5m", {}).get("passed"):
            return "five_minute_filter"
    return None


def _no_trade_explanation(decisions, setups, accepted_ids, stage_counts):
    if accepted_ids:
        return f"Observed {len(accepted_ids)} accepted order(s); rejection counts cover other blocked evaluations."
    if not decisions:
        return "No decision records were available to explain trading activity."
    directions = "BUY/SELL" if any("sell_evaluation" in record or
                                   _selected_evaluation(record)[0] == "SELL"
                                   for record in decisions) else "BUY"
    if not setups and set(stage_counts) == {"stale_data"}:
        explanation = f"Trading evaluations were blocked by stale or unavailable data; no qualifying {directions} setup or accepted order was observed."
    elif not setups:
        explanation = f"No qualifying closed-candle {directions} setup was observed."
    else:
        explanation = f"Observed {len(setups)} qualifying closed-candle {directions} setup(s), but no accepted order was logged."
    labels = {
        "no_qualifying_setup": "entry rules", "stale_data": "stale or unavailable data",
        "outside_entry_window": "entry timing", "five_minute_filter": "5m filter",
        "risk": "risk limits", "execution": "execution conditions or unresolved orders",
    }
    blockers = [f"{labels[stage]} ({count})" for stage, count in stage_counts.items() if count]
    if blockers:
        explanation += " Recorded blockers by unique entry candle and direction: " + ", ".join(blockers) + "."
    return explanation


def summarize(path):
    decisions = []
    accepted_ids = set()
    for line in Path(path).read_text().splitlines():
        for marker in ("[Decision] ", "[Execution] "):
            if marker not in line:
                continue
            try:
                payload = json.loads(line.split(marker, 1)[1])
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            if marker == "[Decision] " and isinstance(payload.get("evaluated_at"), (int, float)):
                decisions.append(payload)
            elif payload.get("status") == "accepted" and payload.get("id"):
                accepted_ids.add(payload["id"])
    decisions.sort(key=lambda record: record["evaluated_at"])
    timestamps = sorted({record["evaluated_at"] for record in decisions})
    gaps = [right - left for left, right in zip(timestamps, timestamps[1:])]
    setups, eligible, eligible_five_seconds, filtered, evaluated_entries = set(), set(), set(), set(), set()
    evaluated_directional_entries = set()
    reasons = Counter()
    failures, unique_reasons, rejections = set(), set(), set()
    for record in decisions:
        direction, gate = _selected_evaluation(record)
        entry_ts = gate.get("entry_ts")
        reason = record.get("reason", "unknown")
        reasons[reason] += 1
        # A missing entry is one unavailable-data episode per asset and period,
        # rather than a new candle for every poll. Keep it out of candle totals.
        period = _entry_period(record, gate)
        key = (record.get("asset"), period, entry_ts, direction)
        unique_reasons.add((key, reason))
        stage = _rejection_stage(record, gate)
        if stage:
            rejections.add((key, stage, reason))
        if entry_ts is not None:
            evaluated_entries.add(key[:3])
            failures.update((key, rule) for rule in gate.get("failed_rules", []))
        trend_filter = record.get("trend_filter_5m", {})
        filter_direction = trend_filter.get("required_direction", direction)
        for side, evaluation in _evaluations(record).items():
            side_entry_ts = evaluation.get("entry_ts")
            if side_entry_ts is None:
                continue
            side_period = _entry_period(record, evaluation)
            side_key = (record.get("asset"), side_period, side_entry_ts, side)
            evaluated_entries.add(side_key[:3])
            evaluated_directional_entries.add(side_key)
            if not evaluation.get("passed"):
                continue
            setups.add(side_key)
            if not _fresh_data(record) or not _within_entry_window(record, side_entry_ts, evaluation):
                continue
            eligible.add(side_key)
            if 0 <= record["evaluated_at"] - (side_entry_ts + side_period) <= 5:
                eligible_five_seconds.add(side_key)
            if filter_direction == side and trend_filter.get("passed"):
                filtered.add(side_key)
    # Required history can arrive at different times. A candle that later meets
    # every rule is a qualifying setup, even if earlier polls failed its rules.
    rejections = {(key, stage, reason) for key, stage, reason in rejections
                  if stage != "no_qualifying_setup" or key not in setups}
    stage_entries = {(key, stage) for key, stage, _ in rejections}
    stage_counts = dict(sorted(Counter(stage for _, stage in stage_entries).items()))
    rejection_reasons = {(key, reason) for key, _, reason in rejections}
    elapsed = timestamps[-1] - timestamps[0] if len(timestamps) > 1 else 0
    per_direction = {
        direction: {
            "unique_confirmed_setups": sum(key[-1] == direction for key in setups),
            "fresh_setups_in_five_second_entry_window": sum(key[-1] == direction for key in eligible_five_seconds),
            "fresh_setups_in_entry_window": sum(key[-1] == direction for key in eligible),
            "setups_also_passing_logged_5m_filter": sum(key[-1] == direction for key in filtered),
            "evaluated_entry_candles": sum(key[-1] == direction for key in evaluated_directional_entries),
        } for direction in ("BUY", "SELL")
    }
    return {
        "log": str(path), "observed_seconds": round(elapsed, 3),
        "decision_records": len(decisions),
        "median_evaluation_interval_seconds": round(statistics.median(gaps), 4) if gaps else None,
        "unique_confirmed_buy_setups": per_direction["BUY"]["unique_confirmed_setups"],
        "unique_confirmed_sell_setups": per_direction["SELL"]["unique_confirmed_setups"],
        "unique_confirmed_setups": len(setups),
        # Preserve the meaning of historical BUY-only summary fields.
        "fresh_setups_in_five_second_entry_window": per_direction["BUY"]["fresh_setups_in_five_second_entry_window"],
        "fresh_setups_in_entry_window": per_direction["BUY"]["fresh_setups_in_entry_window"],
        "setups_also_passing_logged_5m_filter": per_direction["BUY"]["setups_also_passing_logged_5m_filter"],
        "total_fresh_setups_in_five_second_entry_window": len(eligible_five_seconds),
        "total_fresh_setups_in_entry_window": len(eligible),
        "total_setups_also_passing_logged_5m_filter": len(filtered),
        "setup_counts_by_direction": per_direction,
        "observed_accepted_order_ids": len(accepted_ids),
        "decision_reasons": dict(reasons),
        "evaluated_entry_candles": len(evaluated_entries),
        "decision_reasons_per_unique_entry": dict(sorted(Counter(reason for _, reason in unique_reasons).items())),
        "rejection_stages_per_unique_entry": stage_counts,
        "rejection_reasons_per_unique_entry": dict(sorted(Counter(reason for _, reason in rejection_reasons).items())),
        "no_trade_explanation": _no_trade_explanation(decisions, setups, accepted_ids, stage_counts),
        "failed_rules_per_unique_setup": dict(Counter(rule for _, rule in failures)),
        "failed_rules_by_direction": {
            direction: dict(Counter(rule for key, rule in failures if key[-1] == direction))
            for direction in ("BUY", "SELL")
        },
        "target_trades_per_hour": [10, 15],
        "frequency_target_verified": False,
        "limitations": [
            "Counts describe this recorded interval; no trades or outcomes are simulated.",
            "Repeated evaluations of one asset/entry-period/candle/direction setup count once; risk limits and open orders may block candidates.",
            "Historical freshness/filter summary fields remain BUY-only; total_* and setup_counts_by_direction include SELL.",
            "Filter counts require the logged filter to pass for the same direction as the setup.",
            "Rule failures and rejection stages describe the selected diagnostic side, not failures of the opposite gate.",
            "decision_reasons counts polls; unique-entry rejection counts do not. A candle can encounter multiple blockers.",
            "Missing entry timestamps count as one unavailable-data episode per asset, entry period and diagnostic direction, outside evaluated_entry_candles.",
            "Entry periods missing from archived logs are interpreted as 60 seconds.",
            "Older logs may report fresh quotes without recording closed-history freshness.",
            "Logged filter results reflect the version running when the log was written.",
            "A market-dependent 10–15 trades/hour rate is not guaranteed by these counts.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.log), indent=2))
