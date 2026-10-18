"""Verified chart display settings, independent of closed-candle strategy data."""
import asyncio
import re

from playwright.async_api import expect


PROFILES = {
    "entry": {"timeframe": "1m", "timeframe_seconds": 60,
              "indicators": [("EMA", 9), ("EMA", 21), ("RSI", 14)]},
    "trend": {"timeframe": "30s", "timeframe_seconds": 30,
              "indicators": [("EMA", 20), ("EMA", 50), ("RSI", 14)]},
    "filter5m": {"timeframe": "5m", "timeframe_seconds": 300,
                 "indicators": [("EMA", 9), ("EMA", 21), ("RSI", 14)]},
    "entry30s": {"timeframe": "30s", "timeframe_seconds": 30,
                  "indicators": [("EMA", 9), ("EMA", 21), ("RSI", 14)]},
}
LEGEND = '[data-test="techanalysis-active-tool-legend"]'
TITLE = '[data-test="tech-analysis-legend-title"]'
CONTENT = '[data-test="techanalysis-active-instrument-panel-content"]'
TIMEFRAME = '[data-test="TimeFrameSelector.selected"]'
CONTROL_READY_MS = 15000
CONFIGURE_ATTEMPTS = 3
TRADES_PANEL = '[data-test="cor-w-panel-trades"]'
TIMEFRAME_MENU = '[data-test="TimeFrameSelector.selector"]'
INDICATOR_PANEL = '[data-test$="_ChartIndicators"]:visible'
INDICATOR_SEARCH = '[data-test="techanalysis-instrument-search-field"]'


def _display_result(view=None, observed=None):
    profile = PROFILES.get(view)
    return {"source": "broker_ui", "view": view, "verified": False,
            "timeframe_seconds": profile["timeframe_seconds"] if profile else None,
            "expected": {"timeframe": profile["timeframe"], "indicators": [
                {"name": name, "period": period} for name, period in profile["indicators"]
            ]} if profile else None,
            "observed": observed, "changed": False}


async def _read_chart(page):
    selected = page.locator(TIMEFRAME)
    timeframe = (await selected.inner_text()).strip() if await selected.count() == 1 else None
    indicators = []
    legends = page.locator(LEGEND)
    for index in range(await legends.count()):
        legend = legends.nth(index)
        title = legend.locator(TITLE)
        content = legend.locator(CONTENT)
        name = (await title.inner_text()).strip() if await title.count() == 1 else "unknown"
        text = (await content.inner_text()).strip() if await content.count() == 1 else ""
        numbers = re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", text)
        # The first legend parameter is the period; RSI may also show its bands.
        period = int(numbers[0]) if numbers and float(numbers[0]).is_integer() else None
        indicators.append({"name": name, "period": period})
    return {"timeframe": timeframe, "indicators": indicators}


def _matches(observed, profile):
    actual = [(item["name"], item["period"]) for item in observed["indicators"]]
    return (observed["timeframe"] == profile["timeframe"]
            and sorted(actual, key=str) == sorted(profile["indicators"], key=str))


async def probe_strategy_chart(page, *, preferred_view=None):
    """Read the selected chart profile without opening menus or changing the UI.

    RSI on the 5-minute view is a display aid; it is not an extra strategy gate.
    Missing controls may mean legends are unmounted, so only reconciliation after
    the indicator menu has mounted can safely decide which instances to add.
    A preferred view disambiguates profiles sharing the selected interval; it
    never overrides the user's selected interval. Without one, 30s shows trend.
    """
    if preferred_view is not None and preferred_view not in PROFILES:
        raise ValueError(f"Unknown preferred chart view: {preferred_view}")
    result = _display_result()
    if page is None:
        result["reason"] = "page_unavailable"
        return result
    try:
        if await page.locator(TRADES_PANEL).is_visible():
            result["reason"] = "trades_panel_open"
            return result
        selected = page.locator(TIMEFRAME)
        if not await selected.is_visible() or not await page.locator('[data-test="indicator-menu"]').is_visible():
            result["reason"] = "chart_controls_unavailable"
            return result
        observed = await _read_chart(page)
        preferred = PROFILES.get(preferred_view)
        view = (preferred_view if preferred and preferred["timeframe"] == observed["timeframe"]
                else next((name for name, profile in PROFILES.items()
                           if profile["timeframe"] == observed["timeframe"]), None))
        result = _display_result(view, observed)
        if view is None:
            result["reason"] = "unsupported_timeframe"
            return result
        if any([await page.locator(TIMEFRAME_MENU).is_visible(),
                await page.locator(INDICATOR_PANEL).is_visible(),
                await page.locator(INDICATOR_SEARCH).is_visible()]):
            result["reason"] = "chart_controls_busy"
            return result
        if (await selected.inner_text()).strip() != observed["timeframe"]:
            result["reason"] = "timeframe_changed"
            return result
        result["verified"] = _matches(observed, PROFILES[view])
        result["reason"] = "already_matched" if result["verified"] else "indicator_mismatch"
    except Exception as exc:
        result["reason"] = "chart_probe_failed"
        result["error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
    return result


class _ChartChanged(Exception):
    pass


async def reconcile_selected_strategy_chart(page, timeout_seconds=12, *, preferred_view=None):
    """Boundedly match indicators to the user's currently selected timeframe.

    This helper never selects an interval or closes a trades panel. Call it while
    holding the runtime's UI/execution lock, outside the entry timing window.
    Busy controls are left to their current owner. A user interval change stops
    this attempt; a later attempt will pick up the newly selected profile.
    ``preferred_view`` chooses the indicator role when profiles share an interval.
    """
    if preferred_view is not None and preferred_view not in PROFILES:
        raise ValueError(f"Unknown preferred chart view: {preferred_view}")
    result = _display_result()
    opened_catalog = False

    async def reconcile():
        nonlocal result, opened_catalog
        result = await probe_strategy_chart(page, preferred_view=preferred_view)
        if result.get("reason") != "indicator_mismatch":
            return
        profile = PROFILES[result["view"]]

        async def guard():
            if await page.locator(TRADES_PANEL).is_visible():
                raise _ChartChanged("trades_panel_open")
            selected = page.locator(TIMEFRAME)
            if not await selected.is_visible():
                raise _ChartChanged("chart_controls_unavailable")
            if (await selected.inner_text()).strip() != profile["timeframe"]:
                raise _ChartChanged("timeframe_changed")
            if await page.locator(TIMEFRAME_MENU).is_visible():
                raise _ChartChanged("chart_controls_busy")

        await guard()
        # Read again after mounting. Saved instances can be absent from the DOM
        # until this panel is opened; adding from the earlier probe duplicates them.
        opened_catalog = True
        await page.locator('[data-test="indicator-menu"]').click(timeout=2500)
        catalog = page.locator('[data-test="techanalysis-instrument-list-item-title"] '
                               '[data-trans="indicators_menu_title_ema"]')
        await catalog.wait_for(state="visible", timeout=2500)
        await guard()
        observed = await _read_chart(page)
        result["observed"] = observed
        missing = list(profile["indicators"])
        removals = []
        for index, item in enumerate(observed["indicators"]):
            key = (item["name"], item["period"])
            if key in missing:
                missing.remove(key)
            else:
                removals.append(index)
        for index in reversed(removals):
            await guard()
            legends = page.locator(LEGEND)
            count = await legends.count()
            await legends.nth(index).locator('[data-test="tech-analysis-legend-remove"]').click(timeout=2500)
            result["changed"] = True
            await expect(legends).to_have_count(count - 1, timeout=2500)
        for name, period in missing:
            await guard()
            # A partially saved addition is still a change. The next attempt reads
            # actual legends and repairs it instead of blindly adding it again.
            result["changed"] = True
            await _add_indicator(page, name, period, guard=guard)
        for _ in range(10):
            await guard()
            result["observed"] = await _read_chart(page)
            await guard()
            if _matches(result["observed"], profile):
                result.update(verified=True, reason="configured" if result["changed"] else "already_matched")
                return
            await asyncio.sleep(0.1)
        result["reason"] = "indicator_readback_mismatch"

    try:
        await asyncio.wait_for(reconcile(), timeout=timeout_seconds)
    except _ChartChanged as exc:
        result.update(verified=False, reason=str(exc))
    except asyncio.TimeoutError:
        result.update(verified=False, reason="chart_sync_timeout")
    except Exception as exc:
        result.update(verified=False, reason="display_configuration_failed",
                      error=f"{type(exc).__name__}: {str(exc)[:240]}")
    finally:
        if opened_catalog:
            # Closing only the panel opened here avoids claiming the user's menus.
            # Cancellation must propagate; cleanup itself stays below three seconds.
            close = page.locator('[data-test="cor-w-panel-techAnalysisList"] [data-test="cor-w-panel-close"]')
            try:
                if await close.is_visible():
                    await close.click(timeout=2000)
            except Exception:
                pass
    # Cleanup is another await: the user may switch intervals during it. Never
    # report a previous profile as verified for the newly selected timeframe.
    if result["verified"]:
        try:
            observed = await asyncio.wait_for(_read_chart(page), timeout=0.5)
            result["observed"] = observed
            if not _matches(observed, PROFILES[result["view"]]):
                result.update(verified=False, reason="chart_changed_after_configuration")
        except Exception:
            result.update(verified=False, reason="chart_readback_unavailable")
    return result


async def set_chart_timeframe(page, timeframe: str) -> bool:
    """Use the chart interval selector only, and require its exact readback."""
    if page is None or timeframe not in {profile["timeframe"] for profile in PROFILES.values()}:
        return False
    try:
        selected = page.locator(TIMEFRAME)
        await selected.wait_for(state="visible", timeout=CONTROL_READY_MS)
        if (await selected.inner_text()).strip() == timeframe:
            return True
        await selected.click(timeout=2500)
        menu = page.locator('[data-test="TimeFrameSelector.selector"]')
        await menu.wait_for(state="visible", timeout=2500)
        choice = menu.locator('[data-test="TimeFrameSelector.selector.button"]').filter(
            has_text=re.compile(r"^\s*" + re.escape(timeframe) + r"\s*$"))
        if await choice.count() != 1:
            return False
        await choice.click(timeout=2500)
        await expect(selected).to_have_text(re.compile(r"^\s*" + re.escape(timeframe) + r"\s*$"), timeout=2500)
        return True
    except Exception:
        return False


async def _add_indicator(page, name, period, guard=None):
    async def check():
        if guard is not None:
            await guard()

    catalog = page.locator('[data-test="techanalysis-instrument-list-item-title"] '
                           f'[data-trans="indicators_menu_title_{name.lower()}"]')
    search = page.locator('[data-test="techanalysis-instrument-search-field"]')
    if not await search.is_visible():
        await check()
        await page.locator('[data-test="indicator-menu"]').click(timeout=2500)
    if name == "RSI" and not await catalog.is_visible():
        section = page.locator('[data-test="indicators-button-show-oscillators"]')
        if await section.is_visible():
            await check()
            await section.click(timeout=2500)
    await catalog.wait_for(state="visible", timeout=2500)
    previous_count = await page.locator(LEGEND).count()
    await check()
    await catalog.click(timeout=2500)
    await expect(page.locator(LEGEND)).to_have_count(previous_count + 1, timeout=2500)
    # Each catalog click adds one indicator immediately. Select that new instance,
    # rather than the first EMA legend, so the two EMA periods remain distinct.
    added = page.locator(LEGEND).nth(previous_count)
    await check()
    await added.locator(TITLE).click(timeout=2500)
    panel = page.locator('[data-test$="_ChartIndicators"]:visible')
    await panel.wait_for(state="visible", timeout=2500)
    period_input = panel.locator('[data-test="valueInputField"] input').first
    await check()
    await period_input.fill(str(period), timeout=2500)
    await check()
    await period_input.press("Tab")
    if (await period_input.input_value()).strip() != str(period):
        raise ValueError(f"{name} period input did not retain {period}")
    await check()
    await panel.locator('[data-test="indicatorsFormDone"]').click(timeout=2500)


async def _prepare_chart_controls(page):
    # A trades panel can unmount full chart legends. Empty locators do not prove
    # that no saved indicators exist. Render the chart controls before reading.
    close = page.locator('[data-test="cor-w-panel-trades"] [data-test="cor-w-panel-close"]')
    if await close.is_visible():
        await close.click(timeout=2500)
    catalog = page.locator('[data-test="techanalysis-instrument-list-item-title"] '
                           '[data-trans="indicators_menu_title_ema"]')
    if not await catalog.is_visible():
        # Asset title and account balance become available before chart tools.
        # A missing menu during this loading phase is not a failed selector.
        menu = page.locator('[data-test="indicator-menu"]')
        await menu.wait_for(state="visible", timeout=CONTROL_READY_MS)
        await menu.click(timeout=5000)
    await catalog.wait_for(state="visible", timeout=CONTROL_READY_MS)
    await asyncio.sleep(0.5)


async def _configure_strategy_chart_once(page, view="entry"):
    """Apply an exact indicator profile and return observed, verified UI state.

    A display failure never changes or invalidates native strategy candles. The
    caller must log verified=False instead of claiming an unseen UI change.
    """
    if view not in PROFILES:
        raise ValueError(f"Unknown chart view: {view}")
    profile = PROFILES[view]
    result = {"source": "broker_ui", "view": view, "verified": False,
              "timeframe_seconds": profile["timeframe_seconds"],
              "expected": {"timeframe": profile["timeframe"], "indicators": [
                  {"name": name, "period": period} for name, period in profile["indicators"]]},
              "observed": None, "changed": False}
    if page is None:
        result["reason"] = "page_unavailable"
        return result
    try:
        # Configure the candle interval independently of the indicators menu.
        # Slow indicator loading must not leave the default 5s interval behind.
        before = await _read_chart(page)
        if not await set_chart_timeframe(page, profile["timeframe"]):
            result["reason"] = "timeframe_not_verified"
            result["observed"] = await _read_chart(page)
            return result
        result["changed"] = before["timeframe"] != profile["timeframe"]
        await _prepare_chart_controls(page)
        observed = await _read_chart(page)
        result["observed"] = observed
        if _matches(observed, profile):
            result.update(verified=True, reason="configured" if result["changed"] else "already_matched")
            return result
        if not await set_chart_timeframe(page, profile["timeframe"]):
            result["reason"] = "timeframe_not_verified"
            result["observed"] = await _read_chart(page)
            return result
        result["changed"] |= observed["timeframe"] != profile["timeframe"]
        # Only indicator legends have these selectors. Drawing tools are untouched.
        missing = list(profile["indicators"])
        await asyncio.sleep(0.5)
        observed = await _read_chart(page)
        removals = []
        for index, item in enumerate(observed["indicators"]):
            key = (item["name"], item["period"])
            if key in missing:
                missing.remove(key)
            else:
                removals.append(index)
        for index in reversed(removals):
            legends = page.locator(LEGEND)
            count = await legends.count()
            await legends.nth(index).locator('[data-test="tech-analysis-legend-remove"]').click(timeout=2500)
            await expect(legends).to_have_count(count - 1, timeout=2500)
            result["changed"] = True
        for name, period in missing:
            await _add_indicator(page, name, period)
            result["changed"] = True
        # Broker state propagation can lag the Done click; require final legends.
        for _ in range(10):
            observed = await _read_chart(page)
            result["observed"] = observed
            if _matches(observed, profile):
                result.update(verified=True, reason="configured")
                return result
            await asyncio.sleep(0.1)
        result["reason"] = "indicator_readback_mismatch"
    except Exception as exc:
        result["reason"] = "display_configuration_failed"
        result["error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
        try:
            result["observed"] = await _read_chart(page)
        except Exception:
            pass
    finally:
        # Return the usable chart to the user after verifying its settings.
        close = page.locator('[data-test="cor-w-panel-techAnalysisList"] [data-test="cor-w-panel-close"]')
        try:
            if await close.is_visible():
                await close.click(timeout=2500)
        except Exception:
            pass
    return result


async def configure_strategy_chart(page, view="entry"):
    """Recover transient chart mounting/asset changes by re-reading actual state.

    Every attempt reconciles the actual remaining indicators, so a timeout after
    a successful Add/Done click cannot blindly create duplicate instances.
    """
    changed = False
    for attempt in range(1, CONFIGURE_ATTEMPTS + 1):
        result = await _configure_strategy_chart_once(page, view)
        changed |= result["changed"]
        result.update(changed=changed, attempts=attempt)
        if result["verified"] or result.get("reason") == "page_unavailable":
            return result
        if attempt < CONFIGURE_ATTEMPTS:
            await asyncio.sleep(0.5)
    return result


async def apply_all_indicators(page) -> bool:
    """Compatibility wrapper: apply only the entry strategy's indicators."""
    return (await configure_strategy_chart(page, "entry"))["verified"]
