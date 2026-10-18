import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

from config import settings
from bot.trade_governor import TradeGovernor

import logging

_LOGGER = logging.getLogger("governor_plugin")
_LOGGER.setLevel(logging.INFO)
_LOGGER.propagate = True
_INSTALLED = {}


def _default_evaluate_one_indicator(closed, *args, **kwargs):
    try:
        import strategies.one_indicator as one_indicator_module
        fn = getattr(one_indicator_module, "evaluate_one_indicator", None)
        if fn is None:
            return {"direction": None, "reason": "no_evaluator"}
        return fn(closed, *args, **kwargs)
    except Exception:
        return {"direction": None, "reason": "no_evaluator"}


_wrapped_evaluate_one_indicator = _default_evaluate_one_indicator


def _module_for_orchestrator(orch):
    candidates = []
    module_name = getattr(orch, "__module__", None) or type(orch).__module__
    if module_name:
        candidates.append(module_name)
    candidates.append(type(orch).__module__)
    for name in candidates:
        module = sys.modules.get(name)
        if module is not None:
            return module
    return None


def _env_bool(name, default):
    value = os.getenv(name, str(default)).strip().lower()
    return value not in {"0", "false", "no", "off", ""} if value else bool(default)


def _compute_strength(ema_value, ema_prev, close_price):
    try:
        ema_value = float(ema_value)
        ema_prev = float(ema_prev)
        close_price = float(close_price)
    except (TypeError, ValueError):
        return 0.0
    if not (float(close_price) > 0 and float(ema_value) == float(ema_value) and float(ema_prev) == float(ema_prev)):
        return 0.0
    return abs(ema_value - ema_prev) / close_price * 10000.0


def _signal_strengths_from_history(history):
    if not history:
        return []
    closes = [float(item["close"]) for item in history if isinstance(item, dict) and "close" in item]
    if len(closes) < 2:
        return []
    from strategies.closed_candle import ema
    ema_values = ema(closes, 9)
    strengths = []
    for idx in range(1, len(ema_values)):
        close_price = closes[idx]
        ema_now = float(ema_values[idx])
        ema_prev = float(ema_values[idx - 1])
        if close_price <= 0:
            continue
        # Feed the governor with all EMA BUY/SELL signals; the pattern gate is
        # applied separately by the strategy wrapper and should not be conflated
        # with the governor's historical strength data.
        if (ema_now > ema_prev and close_price > ema_now) or (ema_now < ema_prev and close_price < ema_now):
            strength = _compute_strength(ema_now, ema_prev, close_price)
            strengths.append(float(strength))
    return strengths


def _compute_strengths(history):
    return _signal_strengths_from_history(history)


def _seed_from_live_feed(orch, governor, original_eval):
    if governor is None:
        return
    feed = getattr(getattr(orch, "bot", None), "live_feed", None)
    if feed is None:
        return
    asset = getattr(orch, "_current_asset", None)
    if not asset:
        return
    period = getattr(settings, "STRATEGY_ENTRY_SECONDS", 30)
    try:
        history = feed.closed(asset, int(period))
    except Exception:
        history = []
    strengths = _signal_strengths_from_history(history)
    if strengths:
        governor.seed(strengths)


def _first_blocker(reason):
    blocker = str(reason or "").strip()
    if blocker in {"no_ema_alignment", "pattern_no_confirmation", "pattern_conflict", "campaign_target_reached"}:
        return blocker
    if blocker.startswith("governor_"):
        return blocker
    if blocker in {"circuit_breaker_cooldown", "daily_loss_limit_reached", "order_unresolved", "risk_rejected",
                    "late_open_window_missed", "stale_or_unavailable_quote", "loss_halt_active", "capacity"}:
        return blocker
    return "none"


def _wrap_evaluate(orch, governor, original_eval):
    def wrapped(closed, *args, **kwargs):
        result = original_eval(closed, *args, **kwargs)
        if governor is None:
            return result

        signal = result.get("direction")
        pattern = result.get("pattern")
        if signal not in {"BUY", "SELL"}:
            try:
                from strategies import ema_pattern as ema_pattern_module
                base = getattr(ema_pattern_module, "_ema_only_decision", None)
                if callable(base):
                    base_result = base(closed, *args, **kwargs)
                    signal = base_result.get("direction")
            except Exception:
                signal = None
        strength = None
        if signal in {"BUY", "SELL"}:
            try:
                ema_now = result.get("ema")
                ema_prev = result.get("ema_prev")
                close_price = result.get("close")
                if ema_now is None or ema_prev is None or close_price is None:
                    closes = [float(item["close"]) for item in closed if isinstance(item, dict) and "close" in item]
                    if len(closes) >= 2:
                        from strategies.closed_candle import ema
                        ema_values = ema(closes, 9)
                        ema_now = ema_values[-1]
                        ema_prev = ema_values[-2]
                        close_price = closes[-1]
                strength = _compute_strength(ema_now, ema_prev, close_price)
                if strength == 0.0:
                    try:
                        from strategies.ema_pattern import _ema_only_decision
                        base_signal = _ema_only_decision(closed, *args, **kwargs)
                        ema_now = base_signal.get("ema")
                        ema_prev = base_signal.get("ema_prev")
                        close_price = base_signal.get("close")
                        strength = _compute_strength(ema_now, ema_prev, close_price)
                    except Exception:
                        strength = None
                if strength is not None:
                    governor.observe(float(strength), now=time.time())
            except Exception:
                strength = None
        else:
            strength = None

        first_blocker = _first_blocker(result.get("reason"))

        current_threshold = governor.threshold()
        payload = dict(result)
        payload["signal"] = signal or None
        payload["pattern"] = pattern or result.get("pattern")
        payload["strength_bp"] = float(strength) if strength is not None else None
        payload["blocked_by"] = result.get("blocked_by") or first_blocker
        payload["governor_threshold"] = current_threshold

        if signal in {"BUY", "SELL"} and result.get("direction") is None and first_blocker != "none":
            payload["blocked_by"] = first_blocker
            payload["reason"] = first_blocker
            governor.maybe_log_state(logger=_LOGGER, now=time.time())
            return payload

        if signal in {"BUY", "SELL"} and first_blocker == "none":
            decision = governor.evaluate(float(strength), now=time.time())
            governor.maybe_log_state(logger=_LOGGER, now=time.time())
            payload["threshold"] = decision.get("threshold")
            payload["taken_last_hour"] = decision.get("taken_last_hour")
            payload["taken_last_24h"] = decision.get("taken_last_24h")
            payload["seconds_since_last"] = decision.get("seconds_since_last")
            payload["governor_reason"] = decision.get("reason")
            payload["governor_threshold"] = decision.get("threshold")
            allowed = bool(decision.get("allow", False))
            if not allowed:
                payload["direction"] = None
                payload["reason"] = f"governor_{decision.get('reason', 'blocked')}"
                payload["blocked_by"] = payload["reason"]
                payload["blocked_signal"] = signal or result.get("direction")
                _LOGGER.info("[Governor] %s", json.dumps({
                    "decision": "SKIP",
                    "reason": payload.get("reason"),
                    "strength": float(strength),
                    "threshold": decision.get("threshold"),
                    "taken_last_hour": decision.get("taken_last_hour"),
                    "taken_last_24h": decision.get("taken_last_24h"),
                    "seconds_since_last": decision.get("seconds_since_last"),
                    "signal": payload.get("signal"),
                    "pattern": payload.get("pattern"),
                    "blocked_by": payload.get("blocked_by"),
                    "governor_threshold": decision.get("threshold"),
                }, sort_keys=True, default=str))
                return payload
            payload["direction"] = payload.get("direction") or signal
            payload["reason"] = result.get("reason") or f"{signal.lower()}_ema_confirmed"
            _LOGGER.info("[Governor] %s", json.dumps({
                "decision": "TAKE",
                "reason": payload.get("reason"),
                "strength": float(strength),
                "threshold": decision.get("threshold"),
                "taken_last_hour": decision.get("taken_last_hour"),
                "taken_last_24h": decision.get("taken_last_24h"),
                "seconds_since_last": decision.get("seconds_since_last"),
                "signal": payload.get("signal"),
                "pattern": payload.get("pattern"),
                "blocked_by": payload.get("blocked_by"),
                "governor_threshold": decision.get("threshold"),
            }, sort_keys=True, default=str))
            return payload

        payload["direction"] = result.get("direction")
        return payload

    return wrapped


def install(orch):
    if orch is None:
        return None
    existing = getattr(orch, "_governor_instance", None)
    module = _module_for_orchestrator(orch)
    if existing is not None:
        _seed_from_live_feed(orch, existing, getattr(module, "evaluate_one_indicator", None) if module is not None else None)
        if not getattr(orch, "_governor_rate_logged", False):
            existing.log_rate(logger=_LOGGER, now=time.time())
            orch._governor_rate_logged = True
        if not getattr(orch, "_governor_install_logged", False):
            _LOGGER.info("[Governor] installed max_per_hour=%s min_spacing=%s percentile=%s window=%s min_history=%s max_per_day=%s state_path=%s reused=True",
                         existing.max_per_hour, existing.min_spacing, existing.percentile, existing.window,
                         existing.min_history, existing.max_per_day, getattr(existing, "state_path", ""))
            orch._governor_install_logged = True
        return existing
    if module is not None and getattr(module, "_governor_instance", None) is not None:
        orch._governor_instance = module._governor_instance
        _seed_from_live_feed(orch, module._governor_instance, getattr(module, "evaluate_one_indicator", None) if module is not None else None)
        if not getattr(orch, "_governor_rate_logged", False):
            module._governor_instance.log_rate(logger=_LOGGER, now=time.time())
            orch._governor_rate_logged = True
        if not getattr(orch, "_governor_install_logged", False):
            _LOGGER.info("[Governor] installed max_per_hour=%s min_spacing=%s percentile=%s window=%s min_history=%s max_per_day=%s state_path=%s reused=True",
                         module._governor_instance.max_per_hour, module._governor_instance.min_spacing, module._governor_instance.percentile,
                         module._governor_instance.window, module._governor_instance.min_history, module._governor_instance.max_per_day,
                         getattr(module._governor_instance, "state_path", ""))
            orch._governor_install_logged = True
        return module._governor_instance
    if not _env_bool("GOVERNOR_ENABLED", True):
        return None
    governor = TradeGovernor(
        max_per_hour=int(os.getenv("GOVERNOR_MAX_PER_HOUR", "5")),
        min_spacing=float(os.getenv("GOVERNOR_MIN_SPACING_SECONDS", "480")),
        percentile=float(os.getenv("GOVERNOR_PERCENTILE", "90")),
        window=int(os.getenv("GOVERNOR_WINDOW", "300")),
        min_history=int(os.getenv("GOVERNOR_MIN_HISTORY", "40")),
        max_per_day=int(os.getenv("GOVERNOR_MAX_PER_DAY", "110")),
        state_path=os.getenv("GOVERNOR_STATE_PATH", "logs/governor_state.json"),
    )
    orch._governor_instance = governor
    original_eval = getattr(module, "evaluate_one_indicator", None) if module is not None else None
    if original_eval is None:
        # If the instance has no module-level entry point, the governor still records
        # state but does not wrap a strategy function.
        return governor
    wrapped_eval = _wrap_evaluate(orch, governor, original_eval or _default_evaluate_one_indicator)
    global _wrapped_evaluate_one_indicator
    _wrapped_evaluate_one_indicator = wrapped_eval
    if module is not None:
        module.evaluate_one_indicator = wrapped_eval
        module._wrapped_evaluate_one_indicator = wrapped_eval
        module._governor_plugin_installed = True
        module._governor_instance = governor
        module._governor_original = original_eval or _default_evaluate_one_indicator
    _seed_from_live_feed(orch, governor, original_eval)
    governor.log_state(logger=_LOGGER, now=time.time())
    if not getattr(orch, "_governor_rate_logged", False):
        governor.log_rate(logger=_LOGGER, now=time.time())
        orch._governor_rate_logged = True
    if not getattr(orch, "_governor_install_logged", False):
        _LOGGER.info("[Governor] installed max_per_hour=%s min_spacing=%s percentile=%s window=%s min_history=%s max_per_day=%s state_path=%s",
                     governor.max_per_hour, governor.min_spacing, governor.percentile, governor.window,
                     governor.min_history, governor.max_per_day, governor.state_path)
        orch._governor_install_logged = True
    supervisor = getattr(orch, "supervisor", None)
    if supervisor is not None and not getattr(supervisor, "_governor_plugin_installed", False):
        original_safe_execute_trade = getattr(supervisor, "safe_execute_trade", None)
        if callable(original_safe_execute_trade):
            async def wrapped_safe_execute_trade(signal, confidence, mode, **kwargs):
                accepted = await original_safe_execute_trade(signal, confidence, mode, **kwargs)
                if accepted and governor is not None:
                    governor.record_taken(time.time())
                return accepted

            supervisor.safe_execute_trade = wrapped_safe_execute_trade
            supervisor._governor_plugin_installed = True
            supervisor._governor_original = original_safe_execute_trade
    return governor


__all__ = ["install"]
