"""Exercise the actual locator/parser against the broker's shared card markup."""
import asyncio

from playwright.async_api import async_playwright

from bot.browser import OlymtradeBot


def card(trade_id, closed):
    return f'''<div data-test="deal-history-card-ftt">
      <div data-test="deal-header"><span data-test="asset-name-EURUSD_OTC">EUR/USD OTC</span></div>
      <div>Trade ID\n{trade_id}</div><div>Amount\nĐ200.00</div>
      <div>Duration\n1 min</div>
      <svg data-test="direction-icon" data-icon="icon-arrowlongup"></svg>
      <div>{"Closed" if closed else "Opened"}</div>
      <span data-test="pnl">+Đ170.00</span>
    </div>'''


def test_active_and_history_cards_share_the_same_data_test_name():
    async def check():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(
                    '<div data-test="cor-w-panel-trades">'
                    '<ul data-test="active-deal-list">' + card("123", False) + '</ul>'
                    '<ul data-test="deal-list">' + card("456", True) + '</ul></div>')
                bot = OlymtradeBot()
                bot._page = page
                active = await bot._order_rows()
                assert len(active) == 1
                assert active[0] == dict(id="123", asset="EUR/USD OTC", amount=200.0,
                                        direction="BUY", duration=60, status="OPEN", pnl=None)
                # An active card's projected P&L is never a settlement.
                assert await bot.get_order_result({"id": "123"}) is None
                assert await bot.get_order_result({"id": "456"}) == {
                    "result": "WIN", "pnl": 170.0, "source": "platform"}
            finally:
                await browser.close()
    asyncio.run(check())


def test_flat_account_requires_explicit_demo_evidence_and_no_active_cards():
    from unittest.mock import AsyncMock
    async def check():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                bot = OlymtradeBot()
                bot._page = page
                bot.is_demo_account = AsyncMock(return_value=True)
                async def panel(body):
                    await page.set_content('<div data-test="cor-w-panel-trades" style="min-height:30px">' + body + '</div>')
                await panel('<ul data-test="active-deal-list"></ul>')
                assert await bot.get_flat_ftt_account_evidence() is None
                message = 'No active Fixed Time trades or orders on this account'
                await panel(message)
                proof = await bot.get_flat_ftt_account_evidence()
                assert proof["source"] == "platform_active_order_list"
                assert proof["demo"] and proof["no_active_ftt"]
                await panel(message + '<ul data-test="active-deal-list">' + card("123", False) + '</ul>')
                assert await bot.get_flat_ftt_account_evidence() is None
                await panel(message)
                bot.is_demo_account = AsyncMock(side_effect=[True] + [False] * 31)
                assert await bot.get_flat_ftt_account_evidence() is None
                bot.is_demo_account = AsyncMock(return_value=False)
                assert await bot.get_flat_ftt_account_evidence() is None
            finally:
                await browser.close()
    asyncio.run(check())
