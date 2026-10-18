import asyncio

from config import settings
from agents.agent_pipeline_supervisor import PipelineSupervisorAgent


class DummyBot:
    async def get_current_price(self):
        return 1.10

    async def is_alive(self):
        return True

    async def select_asset(self, asset):
        return True


class DummyRisk:
    def status(self):
        return {"halted": False}


class DummyAgent:
    asset = settings.ASSET


class DummySelector:
    def analyze_position(self, asset):
        return {"has_open_position": False, "should_add": False}

    def select_best_asset(self, available_assets=None, current_asset=None, exclude_current=False):
        return ("USDJPY-OTC", 0.95, "preferred alt")


async def _run():
    supervisor = PipelineSupervisorAgent(
        bot=DummyBot(),
        risk_manager=DummyRisk(),
        agent1=DummyAgent(),
        agent2=None,
        asset_selector=DummySelector(),
    )
    supervisor._current_asset = settings.ASSET
    return await supervisor.ensure_best_asset(current_asset=settings.ASSET)


def test_configured_asset_is_locked():
    asset = asyncio.run(_run())
    assert asset == settings.ASSET
