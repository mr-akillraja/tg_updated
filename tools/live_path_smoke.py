#!/usr/bin/env python3
"""Offline smoke test for the one-indicator live path.

This harness exercises the real orchestrator decision method without any browser
session, login flow, or asset-selection code.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.live_feed import LiveTickFeed
from bot.trade_governor import TradeGovernor
from multi_agent_orchestrator import MultiAgentTradingSystem


def configure_logging() -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    return root


def make_system(feed: LiveTickFeed, governor: TradeGovernor) -> MultiAgentTradingSystem:
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
    system.bot = SimpleNamespace(
        live_feed=feed,
        last_order={"id": "smoke-order", "amount": 200.0, "accepted_at": time.time()},
        get_asset_payout=lambda asset: 0.85,
    )
    system.supervisor = SimpleNamespace(
        cb=SimpleNamespace(should_stop=lambda: (False, "")),
        safe_execute_trade=lambda *args, **kwargs: True,
        execution_uncertain=False,
        last_execution_reason="",
    )
    async def safe_execute_trade(signal, confidence, mode, **kwargs):
        system.bot.last_order = {"id": "smoke-order", "amount": 200.0, "accepted_at": time.time()}
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
    system._accepted_count = 0
    return system


def tick_series(asset: str, values, *, start_ts: float = 1_700_000_000.0):
    feed = LiveTickFeed()
    for index, price in enumerate(values):
        feed.ingest_tick(asset, float(price), broker_ts=start_ts + index * 30, recv=start_ts + index * 30)
    return feed


async def run_case(label: str, prices, *, governor: TradeGovernor):
    feed = tick_series("EUR/USD OTC", prices)
    candles = feed.closed("EUR/USD OTC", 30)
    if not candles:
        raise AssertionError(f"{label}: no closed candles created")
    closed = candles[0]
    system = make_system(feed, governor)
    opening = {"price": float(closed.get("close", prices[-1])), "recv": time.time()}
    print(f"\n[Smoke] case={label}")
    result = await system._one_indicator_trade(closed, opening)
    print(result)
    governor.log_state(logger=logging.getLogger("smoke-governor"), now=time.time())
    governor.log_rate(logger=logging.getLogger("smoke-rate"), now=time.time())
    return result


def _assert_contains(text: str, needle: str, *, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"{label}: expected substring missing: {needle!r}")


async def main() -> int:
    root = configure_logging()
    os.chdir(ROOT)
    governor = TradeGovernor(
        max_per_hour=100,
        min_spacing=0.0,
        percentile=90.0,
        window=300,
        min_history=1,
        max_per_day=1000,
        state_path=ROOT / "logs" / "smoke_governor_state.json",
        clock=lambda: time.time(),
    )
    governor.seed([2.0, 3.0, 5.0, 7.0, 9.0, 11.0, 13.0, 15.0])

    printed = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            message = record.getMessage()
            if any(token in message for token in ("[Pattern]", "[Governor]", "[OneIndicator]", "[Rate]")):
                printed.append(message)

    capture = CaptureHandler()
    root.addHandler(capture)
    try:
        await run_case("buy_no_pattern", [1.08247, 1.08251, 1.08255, 1.08258, 1.08261, 1.08263, 1.08265, 1.08267], governor=governor)
        await run_case("sell_no_pattern", [1.08265, 1.08262, 1.08260, 1.08258, 1.08257, 1.08255, 1.08253, 1.08251], governor=governor)
        await run_case("pattern_conflict", [1.08240, 1.08245, 1.08252, 1.08258, 1.08264, 1.08268, 1.08275, 1.08269], governor=governor)
        await run_case("governor_skip_low_strength", [1.08200, 1.08210, 1.08215, 1.08218, 1.08222, 1.08224, 1.08230, 1.08233], governor=governor)
        await run_case("governor_take_high_strength", [1.08315, 1.08320, 1.08322, 1.08330, 1.08335, 1.08340, 1.08348, 1.08355], governor=governor)
        await run_case("no_ema_direction", [1.08260, 1.08261, 1.08260, 1.08260, 1.08259, 1.08259, 1.08260, 1.08260], governor=governor)
    finally:
        root.removeHandler(capture)

    joined = "\n".join(printed)
    if not printed:
        raise AssertionError("No live-path log lines were emitted")

    _assert_contains(joined, "pattern_no_confirmation", label="pattern_no_confirmation line")
    _assert_contains(joined, "\"decision\": \"WAIT\"", label="wait decision line")
    _assert_contains(joined, "[Governor] state history=", label="governor state log")
    _assert_contains(joined, "[Rate] history=", label="rate log")
    _assert_contains(joined, "[OneIndicator]", label="one-indicator log")

    print("\n[Smoke] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
