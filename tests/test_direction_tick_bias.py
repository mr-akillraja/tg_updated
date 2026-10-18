from bot.live_feed import candle_momentum


def test_closed_candle_direction_follows_body_and_exposes_tick_bias():
    candle = {
        "open": 1.08009,
        "high": 1.08011,
        "low": 1.08005,
        "close": 1.08006,
        "ticks": 43,
        "up_ticks": 22,
        "down_ticks": 21,
    }
    momentum = candle_momentum(candle)

    assert momentum["direction"] == "down"
    assert momentum["body"] == -3e-05
    assert momentum["tick_bias"] == "up"
