# ai-usage-reporter

A zero-dependency Python CLI that reports per-model daily token usage from
`tokscale graph` into an [ai-plan-insight](../ai-plan-insight) instance. Runs
as a native OS timer (launchd on macOS, systemd user on Linux).

## Install

    pip install -e .[dev]
    ai-usage-reporter install     # writes config template + timer unit
    # edit ~/.config/ai-usage-reporter/config.json to set source_id
    ai-usage-reporter run

See `docs/superpowers/specs/2026-07-02-reporter-design.md` for the design.
