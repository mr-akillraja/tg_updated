import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.live_feed import LiveTickFeed
from bot.trade_governor import TradeGovernor
from multi_agent_orchestrator import MultiAgentTradingSystem

FIXED_NOW = 1_700_000_000.0
TRACE_PATH = ROOT / "tests" / "golden" / "decision_trace.json"
LOG_PATH = ROOT / "tests" / "golden" / "log_before.txt"


def _bar(open_, high, low, close, ts):
    return {"ts": ts, "open": float(open_), "high": float(high), "low": float(low), "close": float(close)}


def _make_feed(candles):
    feed = LiveTickFeed(clock=lambda: FIXED_NOW)
    series = feed._get("EURUSDOTC", 30)
    series.closed.extend(list(candles))
    feed._last["EURUSDOTC"] = {
        "price": float(candles[-1]["close"]),
        "ts": float(candles[-1]["ts"]),
        "recv": FIXED_NOW,
        "prev_price": float(candles[-2]["close"]) if len(candles) > 1 else None,
    }
    return feed


def _make_system(feed: LiveTickFeed, governor: TradeGovernor) -> MultiAgentTradingSystem:
    system = MultiAgentTradingSystem.__new__(MultiAgentTradingSystem)
    system.entry_seconds = 30
    system._current_asset = "EUR/USD OTC"
    system.observe_only = False
    system._accepted_count = 0
    system.active_trades = []
    system._execution_uncertain = False
    system._process_lock_held = True
    system._setup_logged = True
    system._attempted_entries = {}
    system._governor_instance = governor
    system.trade_amount = 200.0
    system.payout_minimum = 0.8
    system._payout_cache = None
    async def get_asset_payout(asset):
        return 0.85

    system.bot = SimpleNamespace(
        live_feed=feed,
        last_order={"id": "golden-order", "amount": 200.0, "accepted_at": FIXED_NOW},
        get_asset_payout=get_asset_payout,
    )
    system.supervisor = SimpleNamespace(
        cb=SimpleNamespace(should_stop=lambda: (False, "")),
        safe_execute_trade=lambda *args, **kwargs: True,
        execution_uncertain=False,
        last_execution_reason="",
    )

    async def safe_execute_trade(signal, confidence, mode, **kwargs):
        system.bot.last_order = {"id": "golden-order", "amount": 200.0, "accepted_at": FIXED_NOW}
        return True

    system.supervisor.safe_execute_trade = safe_execute_trade
    system._execution_lock = asyncio.Lock()
    system.risk = SimpleNamespace(
        approve=lambda *args, **kwargs: (True, ""),
        record_trade=lambda *args, **kwargs: None,
    )
    system._trade_capacity = lambda: (100, 100, 0.0)
    system._check_consecutive_loss_halt = lambda: (False, "")
    system._check_circuit_breaker = lambda: False
    system._setup_already_attempted = lambda *args, **kwargs: False
    system._persist_order_state = lambda *args, **kwargs: None
    return system


CASE_SPECS = [
    (
        "buy_no_pattern",
        [
            _bar(1.08247, 1.08260, 1.08240, 1.08252, 1),
            _bar(1.08252, 1.08265, 1.08248, 1.08258, 2),
            _bar(1.08258, 1.08270, 1.08254, 1.08263, 3),
            _bar(1.08263, 1.08276, 1.08260, 1.08269, 4),
            _bar(1.08269, 1.08280, 1.08266, 1.08274, 5),
            _bar(1.08274, 1.08288, 1.08271, 1.08280, 6),
            _bar(1.08280, 1.08294, 1.08276, 1.08286, 7),
            _bar(1.08286, 1.08299, 1.08281, 1.08293, 8),
        ],
    ),
    (
        "sell_no_pattern",
        [
            _bar(1.08292, 1.08296, 1.08280, 1.08284, 1),
            _bar(1.08284, 1.08290, 1.08269, 1.08274, 2),
            _bar(1.08274, 1.08280, 1.08262, 1.08268, 3),
            _bar(1.08268, 1.08273, 1.08256, 1.08261, 4),
            _bar(1.08261, 1.08267, 1.08249, 1.08254, 5),
            _bar(1.08254, 1.08258, 1.08241, 1.08245, 6),
            _bar(1.08245, 1.08252, 1.08233, 1.08239, 7),
            _bar(1.08239, 1.08246, 1.08225, 1.08229, 8),
        ],
    ),
    (
        "pattern_conflict",
        [
            _bar(1.08220, 1.08260, 1.08205, 1.08225, 1),
            _bar(1.08225, 1.08290, 1.08218, 1.08262, 2),
            _bar(1.08262, 1.08305, 1.08248, 1.08290, 3),
            _bar(1.08290, 1.08325, 1.08276, 1.08315, 4),
            _bar(1.08315, 1.08360, 1.08302, 1.08342, 5),
            _bar(1.08342, 1.08392, 1.08330, 1.08380, 6),
            _bar(1.08380, 1.08420, 1.08362, 1.08405, 7),
            _bar(1.08405, 1.08430, 1.08180, 1.08240, 8),
        ],
    ),
    (
        "governor_skip_low_strength",
        [
            _bar(1.08200, 1.08210, 1.08200, 1.08205, 1),
            _bar(1.08205, 1.08215, 1.08202, 1.08210, 2),
            _bar(1.08210, 1.08220, 1.08208, 1.08215, 3),
            _bar(1.08215, 1.08222, 1.08210, 1.08218, 4),
            _bar(1.08218, 1.08225, 1.08212, 1.08221, 5),
            _bar(1.08221, 1.08228, 1.08217, 1.08224, 6),
            _bar(1.08224, 1.08232, 1.08220, 1.08230, 7),
            _bar(1.08230, 1.08238, 1.08226, 1.08233, 8),
        ],
    ),
    (
        "governor_take_high_strength",
        [
            _bar(1.08310, 1.08325, 1.08300, 1.08318, 1),
            _bar(1.08318, 1.08330, 1.08312, 1.08322, 2),
            _bar(1.08322, 1.08345, 1.08318, 1.08332, 3),
            _bar(1.08332, 1.08350, 1.08326, 1.08338, 4),
            _bar(1.08338, 1.08362, 1.08331, 1.08348, 5),
            _bar(1.08348, 1.08370, 1.08342, 1.08358, 6),
            _bar(1.08358, 1.08394, 1.08352, 1.08380, 7),
            _bar(1.08380, 1.08420, 1.08372, 1.08410, 8),
        ],
    ),
    (
        "governor_spacing",
        [
            _bar(1.09015, 1.09028, 1.09010, 1.09024, 1),
            _bar(1.09024, 1.09035, 1.09018, 1.09028, 2),
            _bar(1.09028, 1.09040, 1.09022, 1.09033, 3),
            _bar(1.09033, 1.09044, 1.09028, 1.09038, 4),
            _bar(1.09038, 1.09051, 1.09032, 1.09045, 5),
            _bar(1.09045, 1.09060, 1.09040, 1.09053, 6),
            _bar(1.09053, 1.09068, 1.09048, 1.09060, 7),
            _bar(1.09060, 1.09074, 1.09054, 1.09070, 8),
        ],
    ),
    (
        "governor_hourly_cap",
        [
            _bar(1.09080, 1.09086, 1.09070, 1.09078, 1),
            _bar(1.09078, 1.09092, 1.09072, 1.09084, 2),
            _bar(1.09084, 1.09097, 1.09080, 1.09090, 3),
            _bar(1.09090, 1.09105, 1.09086, 1.09098, 4),
            _bar(1.09098, 1.09114, 1.09092, 1.09106, 5),
            _bar(1.09106, 1.09122, 1.09100, 1.09114, 6),
            _bar(1.09114, 1.09128, 1.09108, 1.09120, 7),
            _bar(1.09120, 1.09132, 1.09114, 1.09128, 8),
        ],
    ),
    (
        "ema_no_direction",
        [
            _bar(1.08260, 1.08263, 1.08258, 1.08260, 1),
            _bar(1.08260, 1.08264, 1.08259, 1.08261, 2),
            _bar(1.08261, 1.08265, 1.08260, 1.08260, 3),
            _bar(1.08260, 1.08261, 1.08259, 1.08260, 4),
            _bar(1.08259, 1.08260, 1.08258, 1.08259, 5),
            _bar(1.08259, 1.08260, 1.08258, 1.08259, 6),
            _bar(1.08260, 1.08261, 1.08259, 1.08260, 7),
            _bar(1.08260, 1.08262, 1.08258, 1.08260, 8),
        ],
    ),
    (
        "doji_candle",
        [
            _bar(1.09000, 1.09008, 1.08996, 1.09003, 1),
            _bar(1.09003, 1.09010, 1.08998, 1.09006, 2),
            _bar(1.09006, 1.09014, 1.09001, 1.09009, 3),
            _bar(1.09009, 1.09016, 1.09003, 1.09012, 4),
            _bar(1.09012, 1.09020, 1.09005, 1.09017, 5),
            _bar(1.09017, 1.09024, 1.09010, 1.09020, 6),
            _bar(1.09020, 1.09032, 1.09015, 1.09025, 7),
            _bar(1.09025, 1.09046, 1.09017, 1.09035, 8),
        ],
    ),
]


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return tz.fromtimestamp(FIXED_NOW)
        return cls(2024, 1, 1, 12, 0, 0)


async def _run_case(label, candles):
    feed = _make_feed(candles)
    series = feed._get("EURUSDOTC", 30)
    closed = feed.closed("EUR/USD OTC", 30)[0]
    governor = TradeGovernor(
        max_per_hour=100,
        min_spacing=0.0,
        percentile=90.0,
        window=300,
        min_history=1,
        max_per_day=1000,
        state_path=ROOT / "logs" / "golden_governor_state.json",
        clock=lambda: FIXED_NOW,
    )
    system = _make_system(feed, governor)
    opening = {"price": float(closed.get("close", candles[-1]["close"])), "recv": FIXED_NOW}
    result = await system._one_indicator_trade(closed, opening)
    payload = {
        "case": label,
        "decision": result.get("decision"),
        "reason": result.get("reason"),
        "blocked_by": result.get("blocked_by"),
        "order_sent": bool(result.get("order_id") is not None or result.get("decision") in {"BUY", "SELL"}),
        "direction": result.get("direction"),
        "governor_history_length": len(getattr(governor, "strengths", [])),
        "governor_threshold": governor.threshold(),
        "governor_taken_list": list(getattr(governor, "taken", [])),
    }
    return payload, result


async def _trace():
    rows = []
    messages = []
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(stream)

    class Capture(logging.Handler):
        def emit(self, record):
            message = record.getMessage()
            if any(token in message for token in ("[Pattern]", "[Governor]", "[OneIndicator]", "[Timing]", "[Rate]")):
                messages.append(message)

    capture = Capture()
    root.addHandler(capture)
    try:
        for label, candles in CASE_SPECS:
            with patch("multi_agent_orchestrator.time.time", lambda: FIXED_NOW), \
                 patch("multi_agent_orchestrator.datetime", FixedDateTime), \
                 patch("bot.live_feed.time.time", lambda: FIXED_NOW), \
                 patch("bot.trade_governor.time.time", lambda: FIXED_NOW):
                row, _ = await _run_case(label, candles)
            rows.append(row)
    finally:
        root.removeHandler(capture)
        root.removeHandler(stream)
    return rows, messages


async def main():
    rows, messages = await _trace()

    expected_json = json.loads(TRACE_PATH.read_text(encoding="utf-8"))
    assert rows == expected_json, "golden trace changed unexpectedly"

    current_pattern = None
    sanitized_lines = []
    for message in messages:
        if message.startswith("[Pattern] "):
            current_pattern = json.loads(message.split("[Pattern] ", 1)[1])
            sanitized_lines.append(message)
            continue
        if message.startswith("[Timing] "):
            payload = json.loads(message.split("[Timing] ", 1)[1])
            timing = payload["timing"]
            assert "boundary_lag_ms" in timing, "missing boundary_lag_ms in timing"
            assert timing["boundary_lag_ms"] == round((timing["opening_recv"] - timing["open_ts"]) * 1000, 1), (
                f"boundary_lag_ms mismatch: {timing}"
            )
            timing.pop("boundary_lag_ms", None)
            sanitized_lines.append(f"[Timing] {json.dumps(payload, sort_keys=True, default=str)}")
            continue
        if message.startswith("[OneIndicator] "):
            payload = json.loads(message.split("[OneIndicator] ", 1)[1])
            if "ema_side" in payload:
                assert current_pattern is not None, "missing pattern context for ema_side"
                assert payload["ema_side"] == current_pattern.get("ema_direction"), (
                    f"ema_side mismatch: pattern={current_pattern.get('ema_direction')} payload={payload['ema_side']}"
                )
            timing = payload.get("timing")
            if isinstance(timing, dict):
                timing.pop("boundary_lag_ms", None)
            payload.pop("ema_side", None)
            payload.pop("ema_strength_bp", None)
            sanitized_lines.append(f"[OneIndicator] {json.dumps(payload, sort_keys=True, default=str)}")
            continue
        sanitized_lines.append(message)

    expected_log = LOG_PATH.read_text(encoding="utf-8")
    actual_log = "\n".join(sanitized_lines) + "\n"
    assert actual_log == expected_log, "golden smoke log differs from captured live stream"
    print(f"golden trace rows={len(rows)}")


if __name__ == "__main__":
    asyncio.run(main())
