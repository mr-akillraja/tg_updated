# Governor environment variables

These knobs are read by the governance wrapper in `bot/governor_plugin.py` and mapped to `TradeGovernor` in `bot/trade_governor.py`.

- `GOVERNOR_ENABLED` — enable or disable the governor wrapper. Default: `true`
- `GOVERNOR_MAX_PER_HOUR` — hourly trade limit. Default: `5`
- `GOVERNOR_MIN_SPACING_SECONDS` — minimum seconds between accepted trades. Default: `480`
- `GOVERNOR_PERCENTILE` — strength threshold percentile for the rolling window. Default: `90`
- `GOVERNOR_WINDOW` — number of historical strengths used for the percentile threshold. Default: `300`
- `GOVERNOR_MIN_HISTORY` — minimum observed strength samples before governor gating is active. Default: `40`
- `GOVERNOR_MAX_PER_DAY` — daily trade limit. Default: `110`
- `GOVERNOR_STATE_PATH` — JSON state file for persisted takeaway history and strengths. Default: `logs/governor_state.json`

Notes:
- Pattern rejection remains separate from the governor: the EMA signal can still be recorded in history even when the pattern gate blocks the decision.
- The governor threshold stays `None` until enough historical strength samples exist to calculate it.
