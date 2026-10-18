"""Submit exactly one explicitly requested demo order to verify browser execution.

This is an execution test, not a strategy signal. Production gate thresholds are
never bypassed by the orchestrator. Broker acknowledgement and settlement are
required; unresolved submissions persist and prevent another test.
"""
import argparse
import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings
from bot.browser import OlymtradeBot
from bot.process_lock import ProcessLock
from bot.risk import RiskManager, TradeRecord
from models.predictor import Signal
from multi_agent_orchestrator import (MultiAgentTradingSystem, configure_logging,
                                      MAX_TRADES_PER_DAY, DAILY_LOSS_LIMIT)

log = logging.getLogger("demo_execution_test")


async def verify(direction, amount):
    settings.RESET_TODAY_LOGS_ON_STARTUP = False
    settings.TRADE_AMOUNT = amount
    risk = RiskManager(max_daily_trades=MAX_TRADES_PER_DAY, max_daily_loss=DAILY_LOSS_LIMIT)
    bot = OlymtradeBot()
    state_holder = MultiAgentTradingSystem.__new__(MultiAgentTradingSystem)
    state_holder._order_state_path = Path(settings.LOG_DIR) / "execution_state.json"
    if state_holder._order_state_path.exists() and json.loads(state_holder._order_state_path.read_text()):
        raise RuntimeError("Unresolved order exists; reconcile before another test")
    signal = Signal.BUY if direction == "BUY" else Signal.SELL
    try:
        await bot.start()
        if not await bot.login() or not await bot.select_asset(settings.ASSET):
            raise RuntimeError("Login or selected asset could not be verified")
        if not await bot.is_demo_account():
            raise RuntimeError("Demo account must be verified; no order submitted")
        flat_proof = await bot.get_flat_ftt_account_evidence()
        if flat_proof is not None:
            risk.confirm_flat_ftt_account(flat_proof)
        approved, reason = risk.approve(signal, 0.95, "ftt", trade_amount=amount)
        if not approved:
            raise RuntimeError("Risk check rejected execution test: " + reason)
        log.info("[ExecutionSmokeTest] kind=mechanical_demo_test strategy_signal=false side=%s amount=%s duration=60", direction, amount)
        state_holder._persist_order_state({
            "status": "submitting", "kind": "demo_execution_test",
            "asset": settings.ASSET, "direction": direction, "amount": amount,
            "duration": 60, "submitted_at": datetime.now().isoformat(timespec="microseconds")})
        try:
            accepted = await bot.place_ftt_trade(signal, 60, settings.ASSET)
        except Exception:
            log.exception("[ExecutionSmokeTest] status=uncertain retry=false")
            return 2
        if not accepted:
            state_holder._persist_order_state(None)
            log.error("[ExecutionSmokeTest] status=rejected clicks_not_confirmed=true")
            return 1
        order = dict(bot.last_order, timestamp=datetime.now().isoformat(timespec="microseconds"),
                     signal=direction, asset=settings.ASSET, state_key="DEMO_EXECUTION_TEST")
        state_holder._persist_order_state({"status": "accepted", "order": order})
        risk.record_trade(TradeRecord(timestamp=order["timestamp"], asset=settings.ASSET,
                                      mode="ftt", signal=direction, confidence=0.95,
                                      amount=order["amount"], state_key="DEMO_EXECUTION_TEST"))
        log.info("[ExecutionSmokeTest] %s", json.dumps(dict(order, status="accepted")))
        deadline = time.monotonic() + 110
        while time.monotonic() < deadline:
            result = await bot.get_order_result(order)
            if result is not None:
                risk.update_pnl_and_journal(order["timestamp"], result["result"], result["pnl"])
                state_holder._persist_order_state(None)
                log.info("[ExecutionSmokeTest] %s", json.dumps(dict(result, order_id=order["id"], status="settled")))
                return 0
            await asyncio.sleep(2)
        log.warning("[ExecutionSmokeTest] status=settlement_unconfirmed order_id=%s entries=blocked", order["id"])
        return 2
    finally:
        await bot.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direction", required=True, choices=["BUY", "SELL"])
    parser.add_argument("--amount", type=float, default=200.0)
    args = parser.parse_args()
    if args.amount <= 0:
        parser.error("amount must be positive")
    with ProcessLock():
        configure_logging()
        raise SystemExit(asyncio.run(verify(args.direction, args.amount)))
