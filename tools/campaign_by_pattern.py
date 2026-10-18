import json
from collections import defaultdict
from pathlib import Path


def summarize_trade_meta(path):
    """Summarize a trade_meta.jsonl sidecar by pattern and outcome."""
    target = Path(path)
    summary = {}
    by_pattern = defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0, "draws": 0, "net": 0.0})
    if target.exists():
        with target.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pattern = str(record.get("pattern") or record.get("setup") or "unknown").strip()
                if not pattern or pattern == "None":
                    continue
                by_pattern[pattern]["count"] += 1
                result = str(record.get("result") or record.get("decision") or "").upper()
                if result == "WIN":
                    by_pattern[pattern]["wins"] += 1
                    by_pattern[pattern]["net"] += float(record.get("pnl", 0) or 0)
                elif result == "LOSS":
                    by_pattern[pattern]["losses"] += 1
                    by_pattern[pattern]["net"] += float(record.get("pnl", 0) or 0)
                elif result == "DRAW":
                    by_pattern[pattern]["draws"] += 1
                elif result in {"BUY", "SELL", "WAIT", "NO TRADE"}:
                    # keep the pattern counts meaningful even before final settlement.
                    pass
                pnl = record.get("pnl")
                if pnl is not None:
                    try:
                        by_pattern[pattern]["net"] += float(pnl)
                    except (TypeError, ValueError):
                        pass
    for pattern, stats in sorted(by_pattern.items()):
        summary[pattern] = stats
    biggest_group = max((stats["count"] for stats in by_pattern.values()), default=0)
    warn = ""
    if biggest_group and any(stats["count"] < 100 for stats in by_pattern.values()):
        warn = "Warning: comparing groups with fewer than ~100 decided trades each is noise; sample sizes are still too small for a fair campaign comparison."
    summary["warning"] = warn
    return summary
