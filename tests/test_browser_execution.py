import asyncio
import importlib
import json
import math
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.browser import OlymtradeBot, OrderUncertainError, _asset_display_name
from models.predictor import Signal
from config import settings


def test_headless_defaults_to_visible_for_local_browser(monkeypatch):
    env_path = Path(__file__).resolve().parents[1] / "config" / ".env"
    backup = env_path.read_text() if env_path.exists() else None
    backup_path = env_path.with_suffix(".env.bak")
    try:
        if env_path.exists():
            env_path.replace(backup_path)
        monkeypatch.delenv("HEADLESS", raising=False)
        import config.settings as config_settings
        importlib.reload(config_settings)
        assert config_settings.HEADLESS is False
    finally:
        if backup_path.exists():
            backup_path.replace(env_path)
        if backup is None:
            env_path.unlink(missing_ok=True)


@pytest.mark.parametrize("asset", ["EUR/USD OTC", "EURUSD-OTC"])
def test_otc_asset_display_preserved(asset):
    assert _asset_display_name(asset) == "EUR/USD OTC"


@pytest.mark.parametrize("title, valid", [
    ("▲ 1.09556 EUR/USD OTC | Trading platform", True),
    ("▼ 1.09556 EUR/USD | Trading platform", False),
    ("▲ 847.1 BNB OTC | Trading platform", False),
    ("Loading...", False),
    ("▲ 0 EUR/USD OTC | Trading platform", False),
])
def test_quote_requires_exact_instrument(title, valid):
    b = OlymtradeBot()
    b.is_alive = AsyncMock(return_value=True)
    b._page = SimplePage(title)
    quote = asyncio.run(b.get_current_quote("EUR/USD OTC"))
    assert (quote is not None) == valid
    if quote:
        assert quote["source"] == "broker_browser_sample"
        assert quote["timestamp_kind"] == "observation_time"
        assert quote["price"] == 1.09556


class SimplePage:
    def __init__(self, title):
        self.title = AsyncMock(return_value=title)


def test_native_history_parser_preserves_resolution():
    b = OlymtradeBot()
    t = math.floor(time.time() / 30) * 30 - 30
    bar = {"t": t, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15}
    b._receive_market_frame(json.dumps([{"e": 10, "d": [
        {"p": "EURUSD_OTC", "tf": 60, "candles": [dict(bar, t=t - t % 60)]},
        {"p": "EURUSD_OTC", "tf": 30, "candles": [bar]}]}]))
    assert len(b._native_candles[("EURUSDOTC", 30)][1]) == 1
    assert b._native_candles[("EURUSDOTC", 30)][1][0]["ts"] == t
    assert ("EURUSDOTC", 60) in b._native_candles
    assert ("EURUSD", 30) not in b._native_candles


def test_native_history_rejects_invalid_ohlc():
    b = OlymtradeBot()
    b._receive_market_frame(json.dumps([{"d": [{"p": "EURUSD_OTC", "tf": 30,
        "candles": [{"t": 30, "open": 1.1, "high": 1.0, "low": 1.2, "close": 1.15}]}]}]))
    assert b._native_candles[("EURUSDOTC", 30)][1] == []


def test_native_request_returns_only_closed_matching_broker_bars():
    b = OlymtradeBot()
    b.get_current_quote = AsyncMock(return_value={"price": 1.1, "observed_at": time.time()})
    requests = []
    async def evaluate(script, packet):
        requests.append(packet)
        end = math.floor(time.time() / 30) * 30
        candles = [{"t": end + offset, "open": 1.1, "high": 1.2,
                    "low": 1.0, "close": 1.15} for offset in (-60, -30, 0)]
        b._receive_market_frame(json.dumps([{"uuid": packet["uuid"], "d": [
            {"p": "EURUSD_OTC", "tf": 30, "candles": candles}]}]))
        return True
    b._page = MagicMock()
    b._page.evaluate = evaluate
    frame = asyncio.run(b.get_native_candles("EUR/USD OTC", 30, 180))
    assert len(frame) == 2
    assert frame.attrs["source"] == "broker_native_ws"
    assert frame.attrs["timeframe_seconds"] == 30
    assert requests[0]["d"][0]["size"] == 30
    assert requests[0]["d"][0]["pair"] == "EURUSD_OTC"


@pytest.mark.parametrize("failure", ["demo", "quote", "amount", "duration", "payout"])
def test_order_preconditions_prevent_click(failure):
    b, button = execution_bot()
    if failure == "demo": b.is_demo_account.return_value = False
    if failure == "quote": b.get_current_quote.return_value = None
    if failure == "amount": b.set_amount.return_value = False
    if failure == "duration": b._set_ftt_duration.return_value = False
    if failure == "payout": b.get_asset_payout.return_value = None
    assert not asyncio.run(b.place_ftt_trade(Signal.BUY, 60, "EUR/USD OTC"))
    button.click.assert_not_awaited()


def execution_bot():
    b = OlymtradeBot()
    b.is_demo_account = AsyncMock(return_value=True)
    b.get_current_quote = AsyncMock(return_value={"price": 1.1, "observed_at": time.time()})
    b.set_amount = AsyncMock(return_value=True)
    b._set_ftt_duration = AsyncMock(return_value=True)
    b.get_asset_payout = AsyncMock(return_value=0.85)
    button = MagicMock()
    button.is_enabled = AsyncMock(return_value=True)
    button.is_visible = AsyncMock(return_value=True)
    button.click = AsyncMock()
    b._page = MagicMock()
    b._page.locator.return_value = button
    b._order_rows = AsyncMock(return_value=[])
    return b, button


def deadline_bot(monkeypatch):
    b, button = execution_bot()
    clock = {"now": 121.0}
    monkeypatch.setattr("bot.browser.time.time", lambda: clock["now"])
    b.get_current_quote.side_effect = lambda asset: {"price": 1.1, "observed_at": clock["now"]}
    return b, button, clock


@pytest.mark.parametrize("deadline", [120., 121.])
def test_expired_entry_deadline_prevents_preflight(monkeypatch, deadline):
    b, button, _ = deadline_bot(monkeypatch)
    assert not asyncio.run(b.place_ftt_trade(Signal.BUY, 60, entry_deadline=deadline))
    b.get_current_quote.assert_not_awaited()
    b.set_amount.assert_not_awaited()
    button.click.assert_not_awaited()
    assert b.last_order is None


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), -float("inf"), "invalid", {}, True])
def test_malformed_entry_deadline_prevents_click(monkeypatch, deadline):
    b, button, _ = deadline_bot(monkeypatch)
    assert not asyncio.run(b.place_ftt_trade(Signal.BUY, 60, entry_deadline=deadline))
    b.get_current_quote.assert_not_awaited()
    button.click.assert_not_awaited()


@pytest.mark.parametrize("delayed_check", ["amount", "final_demo"])
def test_delayed_preflight_cannot_click_expired_setup_with_fresh_quote(monkeypatch, delayed_check):
    b, button, clock = deadline_bot(monkeypatch)
    demo_checks = 0

    async def delay_amount(*args):
        clock["now"] = 126.
        return True

    async def delay_final_demo():
        nonlocal demo_checks
        demo_checks += 1
        if demo_checks == 2:
            clock["now"] = 126.
        return True

    if delayed_check == "amount":
        b.set_amount.side_effect = delay_amount
    else:
        # Still-fresh quote read at 124 must not authorize a click after 125.
        clock["now"] = 124.
        b.is_demo_account.side_effect = delay_final_demo
    assert not asyncio.run(b.place_ftt_trade(Signal.BUY, 60, entry_deadline=125.))
    assert b.get_current_quote.await_count == 2
    button.click.assert_not_awaited()
    assert b.last_order is None


def test_entry_deadline_rechecked_after_waiting_for_order_lock(monkeypatch):
    b, button, clock = deadline_bot(monkeypatch)

    async def run():
        async with b._order_lock:
            task = asyncio.create_task(b.place_ftt_trade(Signal.BUY, 60, entry_deadline=125.))
            await asyncio.sleep(0)
            clock["now"] = 126.
        return await task

    assert not asyncio.run(run())
    b.get_current_quote.assert_not_awaited()
    button.click.assert_not_awaited()


def test_click_in_time_can_be_acknowledged_after_entry_deadline(monkeypatch):
    b, button, clock = deadline_bot(monkeypatch)
    clock["now"] = 124.
    calls = 0

    async def order_rows():
        nonlocal calls
        calls += 1
        if calls == 1:
            return []
        clock["now"] = 126.
        return [{"id": "confirmed-after-deadline", "asset": "EUR/USD OTC",
                 "amount": float(settings.TRADE_AMOUNT), "direction": "BUY", "duration": 60}]

    b._order_rows.side_effect = order_rows
    assert asyncio.run(b.place_ftt_trade(Signal.BUY, 60, "EUR/USD OTC", entry_deadline=125.))
    button.click.assert_awaited_once_with(timeout=1000.)
    assert b.last_order["id"] == "confirmed-after-deadline"
    assert b.last_order["accepted_at"] == 126.


def test_click_timeout_is_bounded_by_deadline_and_remains_uncertain(monkeypatch):
    b, button, clock = deadline_bot(monkeypatch)
    clock["now"] = 124.75
    button.click.side_effect = TimeoutError("Click acknowledgement unknown")
    with pytest.raises(OrderUncertainError):
        asyncio.run(b.place_ftt_trade(Signal.BUY, 60, entry_deadline=125.))
    button.click.assert_awaited_once_with(timeout=250.)
    assert b.last_order is None


def test_quote_becoming_stale_during_preflight_prevents_click():
    b, button = execution_bot()
    b.get_current_quote.side_effect = [
        {"price": 1.1, "observed_at": time.time()},
        {"price": 1.1, "observed_at": time.time() - 3, "stale": True},
    ]
    assert not asyncio.run(b.place_ftt_trade(Signal.BUY, 60, "EUR/USD OTC"))
    button.click.assert_not_awaited()


def test_existing_active_order_prevents_another_click():
    b, button = execution_bot()
    b._order_rows.return_value = [{"id": "already-open", "asset": "GBP/USD"}]
    assert not asyncio.run(b.place_ftt_trade(Signal.BUY, 60, "EUR/USD OTC"))
    button.click.assert_not_awaited()


@pytest.mark.parametrize("signal, suffix", [(Signal.BUY, "up"), (Signal.SELL, "down")])
def test_order_needs_unique_platform_confirmation(signal, suffix):
    b, button = execution_bot()
    b._order_rows.side_effect = [[], [{"id": "confirmed-demo-order", "asset": "EUR/USD OTC", "amount": float(settings.TRADE_AMOUNT), "direction": signal.value, "duration": 60}]]
    assert asyncio.run(b.place_ftt_trade(signal, 60, "EUR/USD OTC"))
    assert b.last_order["id"] == "confirmed-demo-order"
    assert b.last_order["payout"] == 0.85
    assert b.last_order["duration"] == 60
    button.click.assert_awaited_once()
    assert any("deal-submit-button-" + suffix in call.args[0] for call in b._page.locator.call_args_list)


def test_click_without_acknowledgement_is_uncertain(monkeypatch):
    b, button = execution_bot()
    monkeypatch.setattr("bot.browser.asyncio.sleep", AsyncMock())
    with pytest.raises(OrderUncertainError):
        asyncio.run(b.place_ftt_trade(Signal.BUY, 60, "EUR/USD OTC"))
    button.click.assert_awaited_once()
    assert b.last_order is None


def test_result_requires_matching_id_and_broker_pnl():
    b = OlymtradeBot()
    b._order_rows = AsyncMock(return_value=[
        {"id": "other", "status": "WIN", "pnl": "85"},
        {"id": "ours", "status": "OPEN", "pnl": None},
    ])
    assert asyncio.run(b.get_order_result({"id": "ours"})) is None
    b._order_rows.return_value = [{"id": "ours", "status": "LOSS", "pnl": "-200"}]
    assert asyncio.run(b.get_order_result({"id": "ours"}))["pnl"] == -200
