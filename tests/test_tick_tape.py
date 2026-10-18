import json
from datetime import datetime

import pytest

from bot.live_feed import LiveTickFeed
from bot.tick_tape import TickTape


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_tick_tape_records_current_asset_row_fields_and_ignores_other_assets(tmp_path):
    clk = Clock(1717752000.0)
    feed = LiveTickFeed(clock=clk)
    log_dir = tmp_path / "logs"
    tape = TickTape(feed, lambda: "EUR/USD OTC", period=30, ema_period=9, log_dir=str(log_dir), console=False, clock=clk)
    tape.install()

    feed.ingest_tick("EUR/USD OTC", 1.10000, broker_ts=1717751999.9, recv=1717752000.0)
    feed.ingest_tick("EUR/USD OTC", 1.10020, broker_ts=1717752000.9, recv=1717752001.0)
    feed.ingest_tick("GBPUSD", 9.999, broker_ts=1717752002.0, recv=1717752002.0)

    date_tag = datetime.utcfromtimestamp(1717752000.0).strftime("%Y%m%d")
    log_file = log_dir / f"tick_tape_{date_tag}.jsonl"
    assert log_file.exists()
    rows = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2

    second = rows[-1]
    assert second["price"] == pytest.approx(1.10020)
    assert second["delta"] == pytest.approx(0.00020)
    assert second["move"] == "up"
    assert second["gap_ms"] == pytest.approx(1000.0)
    assert second["latency_ms"] == pytest.approx(100.0)
    assert second["pos_in_range"] == pytest.approx(0.7)
    assert second["from_open"] == pytest.approx(0.00020)
    assert second["vs_ema"] == pytest.approx(second["price"] - second["ema"])
    assert second["side"] in {"above", "below", "on"}


def test_tick_tape_wrapper_keeps_rollover_listener_firing(tmp_path):
    clk = Clock(1717752000.0)
    feed = LiveTickFeed(clock=clk)
    events = []
    feed.add_listener(lambda code, period, closed, opening: events.append((code, period, closed, opening)))
    tape = TickTape(feed, lambda: "EURUSD_OTC", period=30, ema_period=9, log_dir=str(tmp_path), console=False, clock=clk)
    tape.install()

    feed.ingest_tick("EURUSD_OTC", 1.10, broker_ts=1717751970.0, recv=1717751970.0)
    clk.t = 1717752001.0
    feed.ingest_tick("EURUSD_OTC", 1.11, broker_ts=1717752001.0, recv=1717752001.0)

    assert any(period == 30 for _, period, _, _ in events)
    assert len(events) >= 1
