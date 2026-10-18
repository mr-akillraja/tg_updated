#!/usr/bin/env python3
"""Read-only measurement: compare tick freshness to the stale cutoff used by the strategy.

This script intentionally does not change bot logic or trade execution. It exercises
LiveTickFeed.last_tick(asset, max_age=...) with the same cutoff used by the one-indicator
path so we can quantify how many observations are considered fresh versus stale.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.live_feed import LiveTickFeed
from config import settings

ASSET = "EUR/USD OTC"
MAX_AGE = float(getattr(settings, "ONE_INDICATOR_STALE_MAX_AGE_SECONDS", 2.0))


def measure(max_age: float = MAX_AGE) -> dict:
    """Measure fresh-vs-stale outcomes across a synthetic tick age ladder."""
    base = 1700000000.0
    feed = LiveTickFeed(clock=lambda: base)
    feed._last["EURUSDOTC"] = {
        "price": 1.08265,
        "ts": base,
        "recv": base,
        "prev_price": None,
    }

    fresh = 0
    stale = 0
    ages = []
    for age in [0.0, 0.5, 1.0, 1.5, 2.0, 2.1, 3.0, 5.0, 10.0]:
        base = 1700000000.0 + age
        tick = feed.last_tick(ASSET, max_age=max_age)
        state = "fresh" if tick is not None else "stale"
        if tick is not None:
            fresh += 1
        else:
            stale += 1
        ages.append({"age_seconds": round(age, 4), "state": state, "tick": tick})

    return {
        "asset": ASSET,
        "cutoff_seconds": max_age,
        "fresh_count": fresh,
        "stale_count": stale,
        "total_samples": len(ages),
        "transition_age": next((row["age_seconds"] for row in ages if row["state"] == "stale"), None),
        "samples": ages,
    }


def main() -> None:
    result = measure()
    print(f"asset={result['asset']}")
    print(f"cutoff_seconds={result['cutoff_seconds']}")
    print(f"fresh_count={result['fresh_count']} stale_count={result['stale_count']} total_samples={result['total_samples']}")
    for row in result["samples"]:
        print(f"age={row['age_seconds']:.4f}s state={row['state']}")


if __name__ == "__main__":
    main()
