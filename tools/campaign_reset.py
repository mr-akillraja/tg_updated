#!/usr/bin/env python3
"""Reset the campaign start cutoff without changing trade logic.

Usage:
  python tools/campaign_reset.py --start-now --yes
  python tools/campaign_reset.py --start 2026-10-16T12:00:00Z --yes

This script is intentionally read-only unless --yes is supplied. It never runs
automatically during startup.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _archive_dir():
    try:
        from config import settings
    except Exception:
        return Path("logs") / "archive"
    log_dir = getattr(settings, "LOG_DIR", "logs")
    archive = getattr(settings, "LOG_ARCHIVE_DIR", os.path.join(log_dir, "archive"))
    return Path(archive)


def _iso_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _payload(start_iso):
    return {
        "start": start_iso,
        "created_at": _iso_now(),
        "source": "campaign_reset.py",
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Create or reset the campaign start marker.")
    parser.add_argument("--start", help="ISO timestamp to begin counting from, e.g. 2026-10-16T12:00:00Z")
    parser.add_argument("--start-now", action="store_true", help="Set the campaign start to the current UTC time")
    parser.add_argument("--yes", action="store_true", help="Write the start marker to disk; otherwise the script is a dry run")
    args = parser.parse_args(argv)

    if args.start and args.start_now:
        parser.error("Choose either --start <ISO> or --start-now, not both.")

    start_iso = args.start or (_iso_now() if args.start_now else _iso_now())
    archive_dir = _archive_dir()
    archive_dir.mkdir(parents=True, exist_ok=True)
    start_path = archive_dir / "campaign_start.json"

    print(f"Would write campaign start to {start_path} with start={start_iso}")
    if not args.yes:
        print("Dry run only. Re-run with --yes to write the file.")
        return 0

    start_path.write_text(json.dumps(_payload(start_iso), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {start_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
