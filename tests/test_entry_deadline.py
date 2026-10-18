"""The supervisor forwards optional entry deadlines without changing legacy calls."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.agent_pipeline_supervisor import PipelineSupervisorAgent
from config import settings
from models.predictor import Signal


@pytest.mark.parametrize("deadline", [None, 125.])
def test_supervisor_forwards_only_provided_entry_deadline(monkeypatch, deadline):
    monkeypatch.setattr(settings, "CONFIDENCE_THRESHOLD", .6)
    bot = SimpleNamespace(place_ftt_trade=AsyncMock(return_value=True))
    risk = SimpleNamespace(approve=lambda *args, **kwargs: (True, ""))
    supervisor = PipelineSupervisorAgent(bot, risk, SimpleNamespace(asset="EUR/USD OTC"), None)
    supervisor._current_asset = "EUR/USD OTC"
    assert asyncio.run(supervisor.safe_execute_trade(Signal.BUY, .95, "ftt", entry_deadline=deadline))
    expected = {"asset": "EUR/USD OTC"}
    if deadline is not None:
        expected["entry_deadline"] = deadline
    bot.place_ftt_trade.assert_awaited_once_with(Signal.BUY, settings.FTT_DURATION, **expected)
    assert not supervisor.execution_uncertain


def test_known_deadline_rejection_does_not_mark_order_uncertain(monkeypatch):
    monkeypatch.setattr(settings, "CONFIDENCE_THRESHOLD", .6)
    bot = SimpleNamespace(place_ftt_trade=AsyncMock(return_value=False))
    risk = SimpleNamespace(approve=lambda *args, **kwargs: (True, ""))
    supervisor = PipelineSupervisorAgent(bot, risk, SimpleNamespace(asset="EUR/USD OTC"), None)
    assert not asyncio.run(supervisor.safe_execute_trade(Signal.BUY, .95, "ftt", entry_deadline=125.))
    assert not supervisor.execution_uncertain
    assert supervisor.last_execution_reason == "broker_order_not_accepted"
