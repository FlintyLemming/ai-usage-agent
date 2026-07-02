# ai-usage-reporter

A zero-dependency Python ≥3.10 CLI that reports per-model daily token usage
from `tokscale graph` into an [ai-plan-insight](../ai-plan-insight) instance.
Runs as a native OS timer: launchd on macOS, systemd user unit on Linux.

## What it does

Each `run` cycle:

1. Invokes `tokscale graph --since <today(UTC+8) - lookback_days>` with
   `TZ=Asia/Shanghai` so tokscale buckets by UTC+8 day.
2. Flattens `contributions[].clients[]` into insight `points[]`, merging
   duplicate `(date, model_id)` across clients by summing input/output.
3. POSTs `{"source_id","source_label","points"}` to
   `{insight_url}/api/usage/report`. ai-plan-insight UPSERTs on
   `(date, source_id, model_id)`, so re-runs are idempotent.
4. If the POST fails (insight unreachable / non-2xx), saves the payload to
   `~/.local/state/ai-usage-reporter/pending/<ts>.json` and retries it on
   the next successful run.

## Install

    pip install -e .[dev]
    ai-usage-reporter install
    # → writes ~/.config/ai-usage-reporter/config.json with source_id="<hostname>"
    #   and a launchd plist (macOS) or systemd user units (Linux).
    # Edit the config to confirm `source_id`, then activate:
    #   macOS:  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai-usage-reporter.plist
    #   Linux:  systemctl --user enable --now ai-usage-reporter.timer

## Run / status / uninstall

    ai-usage-reporter run       # one collection cycle (exit 0 on success)
    ai-usage-reporter status    # last run, pending count, etc.
    ai-usage-reporter uninstall # remove timer units (keeps config + state)

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success (includes empty contributions) |
| 2 | config validation error |
| 3 | tokscale binary missing / not executable |
| 4 | tokscale exited non-zero |
| 5 | tokscale stdout not valid JSON |
| 6 | insight unreachable (connection refused / timeout) |
| 7 | insight returned non-2xx |

## Tests

    python -m pytest -q

Design spec: `docs/superpowers/specs/2026-07-02-reporter-design.md`.
