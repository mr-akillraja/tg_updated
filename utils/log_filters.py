import json
import logging


class MarketDataChangeFilter(logging.Filter):
    """Only emit stable [MarketData] changes and keep a periodic summary."""

    def __init__(self, *, min_change_hold_seconds=2.0, summary_interval_seconds=300.0):
        super().__init__()
        self.min_change_hold_seconds = float(min_change_hold_seconds)
        self.summary_interval_seconds = float(summary_interval_seconds)
        self._last_value = None
        self._last_passed_at = None
        self._pending_value = None
        self._pending_since = None
        self._last_summary_at = None
        self._flips = 0

    @staticmethod
    def _fresh_true_pct(values):
        if not values:
            return 0.0
        truthy = sum(1 for value in values if bool(value))
        return (truthy / len(values)) * 100.0

    def _emit_summary(self, record):
        if self._last_summary_at is not None and (record.created - self._last_summary_at) < self.summary_interval_seconds:
            return
        logger = logging.getLogger(record.name) if getattr(record, "name", None) else logging.getLogger()
        current_value = self._last_value if self._last_value is not None else {}
        logger.info("[MarketData] summary fresh_true_pct=%.2f flips=%s",
                    self._fresh_true_pct(current_value.values()),
                    self._flips)
        self._last_summary_at = record.created

    def filter(self, record):
        message = record.getMessage()
        if not isinstance(message, str) or not message.startswith("[MarketData] "):
            return True

        payload_text = message[len("[MarketData] "):]
        try:
            payload = json.loads(payload_text)
        except (TypeError, ValueError):
            return True

        native_state = payload.get("native_forming_fresh_by_timeframe")
        if not isinstance(native_state, dict):
            return True

        current_value = {str(key): bool(value) for key, value in native_state.items()}

        if self._last_value is None:
            self._last_value = current_value
            self._last_passed_at = record.created
            self._emit_summary(record)
            return True

        if current_value == self._last_value:
            self._emit_summary(record)
            return False

        if self._pending_value == current_value and self._pending_since is not None:
            if (record.created - self._pending_since) > self.min_change_hold_seconds:
                self._last_value = current_value
                self._last_passed_at = record.created
                self._pending_value = None
                self._pending_since = None
                self._flips += 1
                self._emit_summary(record)
                return True
            self._emit_summary(record)
            return False

        if self._last_passed_at is not None and (record.created - self._last_passed_at) > self.min_change_hold_seconds:
            self._last_value = current_value
            self._last_passed_at = record.created
            self._pending_value = None
            self._pending_since = None
            self._flips += 1
            self._emit_summary(record)
            return True

        self._pending_value = current_value
        self._pending_since = record.created
        self._emit_summary(record)
        return False
