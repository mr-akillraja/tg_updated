"""Exercise verified chart controls with local broker-shaped DOM, never orders."""
import asyncio
import json

from playwright.async_api import async_playwright

from bot.chart_indicators import (configure_strategy_chart, probe_strategy_chart,
                                  reconcile_selected_strategy_chart, set_chart_timeframe)


MARKUP = r'''
<button data-test="trade-time-duration-input" onclick="window.orderDurationClicks++">1m</button>
<button data-test="TimeFrameSelector.selected" onclick="openTimeframes()"></button>
<div data-test="TimeFrameSelector.selector" hidden>
  <button data-test="TimeFrameSelector.selector.button" onclick="selectTimeframe('1m')">1m</button>
  <button data-test="TimeFrameSelector.selector.button" onclick="selectTimeframe('30s')">30s</button>
  <button data-test="TimeFrameSelector.selector.button" onclick="selectTimeframe('5m')">5m</button>
</div>
<button data-test="indicator-menu" onclick="window.catalogClicks++; document.querySelector('#catalog').hidden=false">Indicators</button>
<div id="catalog" data-test="cor-w-panel-techAnalysisList" hidden>
  <button data-test="cor-w-panel-close" onclick="closeCatalog()">Close</button>
  <input data-test="techanalysis-instrument-search-field">
  <div data-test="techanalysis-instrument-list-item-title"><span data-trans="indicators_menu_title_ema" onclick="addIndicator('EMA')">EMA</span></div>
  <div data-test="techanalysis-instrument-list-item-title"><span data-trans="indicators_menu_title_rsi" onclick="addIndicator('RSI')">RSI</span></div>
  <div data-test="techanalysis-instrument-list-item-title"><span data-trans="indicators_menu_title_demarker" onclick="addIndicator('DeMarker')">DeMarker</span></div>
</div>
<div id="legends"></div>
<div data-test="drawing-tool" id="drawing">User trend line</div>
<section data-test="dynamic_ChartIndicators" hidden>
  <label data-test="valueInputField">Period <input id="period"></label>
  <label data-test="valueInputField">Upper band <input id="upper" value="70"></label>
  <button data-test="indicatorsFormDone" onclick="saveIndicator()">Done</button>
</section>
<script>
window.orderDurationClicks = 0;
window.catalogClicks = 0;
window.changes = 0;
window.target = null;
function closeCatalog() {
  document.querySelector('#catalog').hidden=true;
  document.querySelector('[data-test$="_ChartIndicators"]').hidden=true;
}
function openTimeframes() {document.querySelector('[data-test="TimeFrameSelector.selector"]').hidden=false;}
function selectTimeframe(value) {
  state.timeframe = value;
  document.querySelector('[data-test="TimeFrameSelector.selector"]').hidden=true;
  window.changes++;
  render();
}
function addIndicator(name) {
  state.indicators.push({name, period: name === 'EMA' ? 20 : 14});
  window.changes++;
  render();
}
function removeIndicator(index) {state.indicators.splice(index,1); window.changes++; render();}
function editIndicator(index) {
  target = index;
  document.querySelector('[data-test$="_ChartIndicators"]').hidden=false;
  document.querySelector('#period').value=state.indicators[index].period;
}
function saveIndicator() {
  if (!state.ignorePeriodSave) {
    state.indicators[target].period=Number(document.querySelector('#period').value);
  }
  document.querySelector('[data-test$="_ChartIndicators"]').hidden=true;
  window.changes++;
  render();
}
function render() {
  document.querySelector('[data-test="TimeFrameSelector.selected"]').textContent=state.timeframe;
  document.querySelector('#legends').innerHTML=state.indicators.map((item,index) =>
    '<div data-test="techanalysis-active-tool-legend">' +
    '<button data-test="tech-analysis-legend-title" onclick="editIndicator('+index+')">'+item.name+'</button>' +
    '<span data-test="techanalysis-active-instrument-panel-content">'+item.period+'</span>' +
    '<button data-test="tech-analysis-legend-remove" onclick="removeIndicator('+index+')">X</button></div>'
  ).join('');
}
</script>
'''


def run_page(initial, check):
    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(MARKUP)
                await page.evaluate("(initial) => {window.state=initial; render();}", initial)
                await check(page)
            finally:
                await browser.close()
    asyncio.run(run())


def test_entry_replaces_bollinger_and_verifies_each_ema_without_order_controls():
    async def check(page):
        result = await configure_strategy_chart(page)
        assert result["verified"], json.dumps(result)
        assert result["observed"] == {
            "timeframe": "1m", "indicators": [
                {"name": "EMA", "period": 9}, {"name": "EMA", "period": 21},
                {"name": "RSI", "period": 14},
            ],
        }
        assert await page.evaluate("window.orderDurationClicks") == 0
        assert await page.locator('[data-test="drawing-tool"]').inner_text() == "User trend line"
        assert await page.locator("#upper").input_value() == "70"
        previous_changes = await page.evaluate("window.changes")
        again = await configure_strategy_chart(page)
        assert again["verified"] and not again["changed"]
        assert again["reason"] == "already_matched"
        assert await page.evaluate("window.changes") == previous_changes
    run_page({"timeframe": "30s", "indicators": [{"name": "Bollinger Bands", "period": 20}]}, check)


def test_trend_profile_removes_duplicate_preserves_matching_ema_and_rsi():
    async def check(page):
        result = await configure_strategy_chart(page, "trend")
        assert result["verified"], json.dumps(result)
        assert result["observed"] == {"timeframe": "30s", "indicators": [
            {"name": "EMA", "period": 20}, {"name": "RSI", "period": 14},
            {"name": "EMA", "period": 50}]}
        assert await page.evaluate("window.orderDurationClicks") == 0
    run_page({"timeframe": "1m", "indicators": [
        {"name": "EMA", "period": 20}, {"name": "EMA", "period": 20},
        {"name": "RSI", "period": 14}]}, check)


def test_successful_form_click_with_wrong_legend_period_is_not_verified():
    async def check(page):
        result = await configure_strategy_chart(page)
        assert not result["verified"]
        assert result["reason"] == "indicator_readback_mismatch"
        assert {"name": "EMA", "period": 20} in result["observed"]["indicators"]
    run_page({"timeframe": "1m", "indicators": [], "ignorePeriodSave": True}, check)


def test_missing_chart_timeframe_never_clicks_order_duration_or_claims_success(monkeypatch):
    monkeypatch.setattr("bot.chart_indicators.CONTROL_READY_MS", 80)
    async def check(page):
        await page.locator('[data-test="TimeFrameSelector.selected"]').evaluate("(node) => node.remove()")
        assert not await set_chart_timeframe(page, "1m")
        result = await configure_strategy_chart(page)
        assert not result["verified"]
        assert result["reason"] == "timeframe_not_verified"
        assert await page.evaluate("window.orderDurationClicks") == 0
        assert await page.evaluate("window.changes") == 0
    run_page({"timeframe": "30s", "indicators": []}, check)

def test_unmounted_legends_are_rendered_before_removing_duplicates():
    async def check(page):
        # Reproduce the broker: saved instances exist while their chart legends
        # are unmounted until the technical analysis panel is opened.
        await page.evaluate("""() => {
            document.querySelector('#legends').innerHTML = '';
            document.querySelector('[data-test="indicator-menu"]').onclick = () => {
                document.querySelector('#catalog').hidden = false;
                render();
            };
        }""")
        result = await configure_strategy_chart(page)
        assert result["verified"], json.dumps(result)
        assert sorted((item["name"], item["period"]) for item in
                      result["observed"]["indicators"]) == [
            ("EMA", 9), ("EMA", 21), ("RSI", 14)]
    run_page({"timeframe": "1m", "indicators": [
        {"name": "Bollinger Bands", "period": 20},
        {"name": "EMA", "period": 9}, {"name": "EMA", "period": 9},
        {"name": "EMA", "period": 21}, {"name": "RSI", "period": 14}]}, check)


def test_delayed_indicator_menu_does_not_leave_default_timeframe_or_fail():
    async def check(page):
        await page.evaluate("""() => {
            const menu = document.querySelector('[data-test="indicator-menu"]');
            menu.remove();
            setTimeout(() => document.body.appendChild(menu), 3200);
        }""")
        result = await configure_strategy_chart(page)
        assert result["verified"], json.dumps(result)
        assert result["observed"]["timeframe"] == "1m"
        assert result["attempts"] == 1
        assert await page.evaluate("window.orderDurationClicks") == 0
    run_page({"timeframe": "5s", "indicators": [
        {"name": "EMA", "period": 9}, {"name": "EMA", "period": 21},
        {"name": "RSI", "period": 14}]}, check)


def test_partial_indicator_save_retries_actual_state_without_duplicate(monkeypatch):
    import bot.chart_indicators as display
    original = display._add_indicator
    calls = []
    async def interrupted(page, name, period):
        await original(page, name, period)
        calls.append((name, period))
        if len(calls) == 1:
            raise TimeoutError("UI response lost after successful save")
    monkeypatch.setattr(display, "_add_indicator", interrupted)
    async def check(page):
        result = await display.configure_strategy_chart(page)
        assert result["verified"], json.dumps(result)
        assert result["attempts"] == 2
        assert len(result["observed"]["indicators"]) == 3
        assert calls.count(("EMA", 9)) == 1
        assert await page.evaluate("window.orderDurationClicks") == 0
    run_page({"timeframe": "5s", "indicators": []}, check)


ENTRY_INDICATORS = [
    {"name": "EMA", "period": 9}, {"name": "EMA", "period": 21},
    {"name": "RSI", "period": 14},
]


def test_short_entry_profile_configures_30s_with_entry_emas_without_order_controls():
    async def check(page):
        result = await configure_strategy_chart(page, "entry30s")
        assert result["verified"], json.dumps(result)
        assert result["view"] == "entry30s"
        assert result["timeframe_seconds"] == 30
        assert result["observed"]["timeframe"] == "30s"
        assert sorted((item["name"], item["period"]) for item in result["observed"]["indicators"]) == [
            ("EMA", 9), ("EMA", 21), ("RSI", 14)]
        before = await page.evaluate("window.changes")
        again = await configure_strategy_chart(page, "entry30s")
        assert again["verified"] and not again["changed"]
        assert await page.evaluate("window.changes") == before
        assert await page.evaluate("window.orderDurationClicks") == 0
        assert await page.locator('[data-test="drawing-tool"]').inner_text() == "User trend line"
    run_page({"timeframe": "1m", "indicators": [
        {"name": "EMA", "period": 20}, {"name": "EMA", "period": 50},
        {"name": "RSI", "period": 14}]}, check)


def test_short_entry_preference_tracks_30s_1m_5m_and_preserves_default_trend_role():
    async def check(page):
        default = await probe_strategy_chart(page)
        assert default["view"] == "trend" and default["verified"]
        short = await probe_strategy_chart(page, preferred_view="entry30s")
        assert short["view"] == "entry30s" and short["reason"] == "indicator_mismatch"
        assert await page.evaluate("window.catalogClicks") == 0
        for timeframe, view in [
            ("30s", "entry30s"), ("1m", "entry"),
            ("5m", "filter5m"), ("30s", "entry30s"),
        ]:
            await page.evaluate("value => selectTimeframe(value)", timeframe)
            result = await reconcile_selected_strategy_chart(page, preferred_view="entry30s")
            assert result["verified"], json.dumps(result)
            assert result["view"] == view
            assert result["observed"]["timeframe"] == timeframe
            assert sorted((item["name"], item["period"]) for item in result["observed"]["indicators"]) == [
                ("EMA", 9), ("EMA", 21), ("RSI", 14)]
            before = await page.evaluate("window.changes")
            again = await reconcile_selected_strategy_chart(page, preferred_view="entry30s")
            assert again["verified"] and not again["changed"]
            assert await page.evaluate("window.changes") == before
        restored = await reconcile_selected_strategy_chart(page)
        assert restored["verified"] and restored["view"] == "trend"
        assert sorted(item["period"] for item in restored["observed"]["indicators"]) == [14, 20, 50]
        assert await page.evaluate("window.orderDurationClicks") == 0
    run_page({"timeframe": "30s", "indicators": [
        {"name": "EMA", "period": 20}, {"name": "EMA", "period": 50},
        {"name": "RSI", "period": 14}]}, check)


def test_selected_profile_follows_1m_30s_5m_without_resetting_user_interval():
    async def check(page):
        initial = await probe_strategy_chart(page)
        assert initial["verified"] and initial["view"] == "entry"
        assert await page.evaluate("window.catalogClicks") == 0
        for timeframe, view, periods in [
            ("1m", "entry", [9, 14, 21]),
            ("30s", "trend", [14, 20, 50]),
            ("5m", "filter5m", [9, 14, 21]),
        ]:
            await page.evaluate("value => selectTimeframe(value)", timeframe)
            result = await reconcile_selected_strategy_chart(page)
            assert result["verified"], json.dumps(result)
            assert result["view"] == view
            assert result["observed"]["timeframe"] == timeframe
            assert sorted(item["period"] for item in result["observed"]["indicators"]) == periods
            clicks = await page.evaluate("window.catalogClicks")
            again = await reconcile_selected_strategy_chart(page)
            assert again["verified"] and not again["changed"]
            assert await page.evaluate("window.catalogClicks") == clicks
        assert await page.evaluate("window.orderDurationClicks") == 0
        assert await page.locator('[data-test="drawing-tool"]').inner_text() == "User trend line"
    run_page({"timeframe": "1m", "indicators": ENTRY_INDICATORS}, check)


def test_unsupported_selected_interval_is_untouched():
    async def check(page):
        for call in (probe_strategy_chart, reconcile_selected_strategy_chart):
            result = await call(page)
            assert not result["verified"] and result["reason"] == "unsupported_timeframe"
        assert await page.evaluate("window.catalogClicks") == 0
        assert await page.evaluate("window.changes") == 0
        assert await page.locator('[data-test="TimeFrameSelector.selected"]').inner_text() == "15m"
    run_page({"timeframe": "15m", "indicators": ENTRY_INDICATORS}, check)


def test_background_reconciliation_skips_trades_panel_and_busy_controls():
    async def check(page):
        await page.evaluate("""() => {
            const panel = document.createElement('div');
            panel.dataset.test = 'cor-w-panel-trades';
            panel.textContent = 'Open trades';
            document.body.appendChild(panel);
        }""")
        result = await reconcile_selected_strategy_chart(page)
        assert result["reason"] == "trades_panel_open"
        assert await page.locator('[data-test="cor-w-panel-trades"]').is_visible()
        await page.locator('[data-test="cor-w-panel-trades"]').evaluate("node => node.remove()")
        for selector in ('[data-test="TimeFrameSelector.selector"]', '#catalog',
                         '[data-test$="_ChartIndicators"]'):
            await page.locator(selector).evaluate("node => node.hidden=false")
            result = await reconcile_selected_strategy_chart(page)
            assert result["reason"] == "chart_controls_busy"
            assert await page.locator(selector).is_visible()
            await page.locator(selector).evaluate("node => node.hidden=true")
        await page.locator('[data-test="indicator-menu"]').evaluate("node => node.remove()")
        assert (await reconcile_selected_strategy_chart(page))["reason"] == "chart_controls_unavailable"
        assert await page.evaluate("window.catalogClicks") == 0
        assert await page.evaluate("window.changes") == 0
    run_page({"timeframe": "30s", "indicators": ENTRY_INDICATORS}, check)


def test_timeframe_change_during_repair_stops_stale_profile_and_recovers_next_attempt():
    async def check(page):
        await page.evaluate("""() => {
            const original = removeIndicator;
            window.removeIndicator = index => {
                original(index);
                selectTimeframe('5m');
            };
        }""")
        result = await reconcile_selected_strategy_chart(page)
        assert not result["verified"] and result["reason"] == "timeframe_changed"
        assert await page.locator('[data-test="TimeFrameSelector.selected"]').inner_text() == "5m"
        assert await page.evaluate("state.indicators.some(item => item.period === 50)") is False
        recovered = await reconcile_selected_strategy_chart(page)
        assert recovered["verified"] and recovered["view"] == "filter5m", json.dumps(recovered)
        assert len(recovered["observed"]["indicators"]) == 3
        assert await page.evaluate("window.orderDurationClicks") == 0
    run_page({"timeframe": "30s", "indicators": ENTRY_INDICATORS}, check)


def test_selected_repair_mounts_saved_legends_before_adding():
    async def check(page):
        await page.evaluate("""() => {
            document.querySelector('#legends').innerHTML = '';
            document.querySelector('[data-test="indicator-menu"]').onclick = () => {
                document.querySelector('#catalog').hidden = false;
                render();
            };
        }""")
        result = await reconcile_selected_strategy_chart(page)
        assert result["verified"] and not result["changed"], json.dumps(result)
        assert await page.evaluate("state.indicators.length") == 3
        assert await page.evaluate("window.changes") == 0
    run_page({"timeframe": "1m", "indicators": ENTRY_INDICATORS}, check)


def test_repair_timeout_reports_partial_state_and_retry_does_not_duplicate(monkeypatch):
    import bot.chart_indicators as display
    original = display._add_indicator
    calls = []

    async def delayed(page, name, period, guard=None):
        await original(page, name, period, guard=guard)
        calls.append((name, period))
        if len(calls) == 1:
            await asyncio.sleep(30)

    monkeypatch.setattr(display, "_add_indicator", delayed)

    async def check(page):
        result = await reconcile_selected_strategy_chart(page, timeout_seconds=1.5)
        assert not result["verified"] and result["reason"] == "chart_sync_timeout", json.dumps(result)
        assert not await page.locator('#catalog').is_visible()
        recovered = await reconcile_selected_strategy_chart(page)
        assert recovered["verified"], json.dumps(recovered)
        assert len(recovered["observed"]["indicators"]) == 3
        assert calls.count(("EMA", 9)) == 1
    run_page({"timeframe": "1m", "indicators": []}, check)


def test_external_cancellation_propagates_and_closes_owned_catalog(monkeypatch):
    import bot.chart_indicators as display

    async def check(page):
        entered = asyncio.Event()

        async def delayed(*args, **kwargs):
            entered.set()
            await asyncio.sleep(30)

        monkeypatch.setattr(display, "_add_indicator", delayed)
        task = asyncio.create_task(reconcile_selected_strategy_chart(page))
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Cancellation must propagate to the runtime")
        assert not await page.locator('#catalog').is_visible()
    run_page({"timeframe": "1m", "indicators": []}, check)


def test_switch_during_cleanup_never_verifies_previous_selected_profile():
    async def check(page):
        await page.evaluate("""() => {
            const original = closeCatalog;
            window.closeCatalog = () => {original(); selectTimeframe('5m');};
        }""")
        result = await reconcile_selected_strategy_chart(page)
        assert not result["verified"]
        assert result["reason"] == "chart_changed_after_configuration"
        assert result["observed"]["timeframe"] == "5m"
    run_page({"timeframe": "30s", "indicators": ENTRY_INDICATORS}, check)
