import logging
import time


class MessageCollapseFilter(logging.Filter):
    """Collapse repeated identical messages without hiding the first few."""

    def __init__(self, window_seconds: float = 30.0, max_emits: int = 3):
        super().__init__()
        self.window_seconds = float(window_seconds)
        self.max_emits = int(max_emits)
        self._entries = {}

    def filter(self, record):
        key = (record.name, record.getMessage())
        now = time.monotonic()
        entry = self._entries.get(key)
        if entry is None:
            self._entries[key] = {"first_ts": now, "count": 1, "last_msg": record.getMessage()}
        else:
            if now - entry["first_ts"] > self.window_seconds:
                entry["first_ts"] = now
                entry["count"] = 1
            else:
                entry["count"] += 1
            entry["last_msg"] = record.getMessage()
        should_emit = entry["count"] <= self.max_emits if entry is not None else True
        if should_emit and logging.getLogger().handlers:
            logging.getLogger().handle(record)
            return False
        return should_emit
