# ai-usage-reporter — Design Spec

**Date:** 2026-07-02
**Scope:** A standalone Python agent that collects per-model daily token usage from a machine (via `tokscale`) and reports it to an ai-plan-insight instance. Runs as a native OS timer service on macOS (launchd) and Linux (systemd).
**Sibling spec:** ai-plan-insight repo — `docs/superpowers/specs/2026-07-02-model-usage-display-design.md` (the receiver/dashboard).

---

## 1. Goal & Context

The user runs ai-plan-insight as a dashboard and wants per-model token-usage history (90/30/7-day stacked bar chart). `tokscale` (Rust) already parses ZCode's `~/.zcode/cli/db/db.sqlite` + JSONL transcripts and aggregates daily-per-model usage. Rather than reinvent parsing, this reporter is a **thin glue layer**: invoke `tokscale graph` (JSON output), reshape to ai-plan-insight's payload, POST it.

One reporter runs per machine; ai-plan-insight sums across machines (keyed by `source_id`). Deployed as an OS-native user timer — no daemon, no socket, no IPC server (same model as tokscale's own `autosubmit` scheduler).

**Language:** Python ≥3.10, **stdlib only** (`json`, `subprocess`, `urllib.request`, `socket`, `os`, `pathlib`, `argparse`, `logging`, `datetime`, `hashlib`). Zero runtime dependencies — pip install is optional; the package is just `python -m reporter`.

---

## 2. Data Flow

```
[tokscale binary]
   │  `tokscale graph --json` (or `-o file`)
   ▼
stdout JSON  (TsTokenContributionData: contributions[].clients[].tokens)
   │
   ▼
[reporter collector]  →  [reporter mapper]  →  points[]
   │
   ▼  POST /api/usage/report
[ai-plan-insight SQLite]  (UPSERT by date+source_id+model_id)
```

Each cycle is idempotent: ai-plan-insight UPSERTs on `(date, source_id, model_id)`, so a repeat run during a day refreshes today's figure without double-counting. Re-running the reporter never corrupts data.

---

## 3. tokscale JSON Shape (verified)

`takscale graph` (default subcommand) emits `TsTokenContributionData`. Relevant fields (camelCase in JSON):

```json
{
  "meta": { "generatedAt": "...", "version": "...", "dateRange": {"start": "...", "end": "..."} },
  "summary": { "totalTokens": N, "totalCost": F, "models": ["glm-5.2", ...], "clients": ["zcode", ...] },
  "contributions": [
    {
      "date": "2026-07-02",
      "totals": { "tokens": N, "cost": F, "messages": N },
      "tokenBreakdown": { "input": N, "output": N, "cacheRead": N, "cacheWrite": N, "reasoning": N },
      "clients": [
        {
          "client": "zcode",
          "modelId": "glm-5.2",
          "providerId": "zhipu",
          "tokens": { "input": N, "output": N, "cacheRead": N, "cacheWrite": N, "reasoning": N },
          "cost": F,
          "messages": N
        }
      ],
      "activeTimeMs": N
    }
  ]
}
```

Source of truth: `/Users/flintylemming/Projects/tokscale/crates/tokscale-cli/src/main.rs` lines 4063–4159 (`TsTokenContributionData`, `TsDailyContribution`, `TsSourceContribution`). Field `contributions[].clients[]` is per-`(client, model)` with a full `TokenBreakdown`.

---

## 4. Mapping (tokscale → insight)

The reporter flattens `contributions[].clients[]` into insight `points[]`. Pseudocode:

```python
points = []
for day in tokscale["contributions"]:
    date = day["date"]                       # "YYYY-MM-DD", UTC+8 (agent runs under TZ=Asia/Shanghai)
    for src in day["clients"]:
        model_id = src["modelId"]            # RAW, e.g. "glm-5.2" — aliasing is insight's job
        tokens = src["tokens"]
        points.append({
            "date": date,
            "model_id": model_id,
            "input_tokens":  tokens.get("input", 0),
            "output_tokens": tokens.get("output", 0),
        })
```

**Deliberately dropped:** `cacheRead`, `cacheWrite`, `reasoning`, `cost`, `messages`, `client`, `providerId`, `activeTimeMs`. insight only charts input+output (YAGNI). If insight later gains a cost view, the reporter can be extended to forward `cost` — the payload schema is additive.

**Aliasing is NOT applied here.** Raw `modelId` is sent as-is; insight applies `model_aliases` at read time. This keeps all alias config in one place (insight's `config.json`) and means renaming an alias re-aggregates history without re-running the reporter.

**Dedup across clients within a day:** if two distinct `client` values report the same `modelId` on the same date (e.g. zcode + opencode both used `glm-5.2`), the mapper produces two points with identical `(date, model_id)`. The uploader MUST merge these before POST (sum input/output), because insight's UPSERT key is `(date, source_id, model_id)` and would otherwise let the second point overwrite the first. The mapper does a final pass:

```python
merged = {}
for p in points:
    key = (p["date"], p["model_id"])
    if key in merged:
        merged[key]["input_tokens"]  += p["input_tokens"]
        merged[key]["output_tokens"] += p["output_tokens"]
    else:
        merged[key] = dict(p)
points = list(merged.values())
```

---

## 5. Configuration

XDG config path: `~/.config/ai-usage-reporter/config.json` (`%APPDATA%` not needed — macOS+Linux only).

```json
{
  "source_id": "macbook-flinty",
  "source_label": "MacBook Pro",
  "insight_url": "http://127.0.0.1:8765",
  "tokscale_bin": "tokscale",
  "tokscale_args": ["graph"],
  "lookback_days": 90,
  "request_timeout_seconds": 30
}
```

- **`source_id`** — REQUIRED. The machine identity. `install` refuses to proceed if unset and writes a template with `source_id: "<hostname>"` for the user to confirm/edit before the first real run. We do NOT auto-derive at runtime because changing the derivation later (e.g. hostname change) would break the UPSERT key and silently fork history.
- **`source_label`** — optional human label, surfaced in insight's `source` table.
- **`insight_url`** — base URL of ai-plan-insight. Reporter POSTs to `{insight_url}/api/usage/report`.
- **`tokscale_bin`** — path or name; resolved via `shutil.which()` at run time. If missing → hard error with install hint.
- **`tokscale_args`** — base args, defaults to `["graph"]` (emits JSON to stdout). The reporter **appends** a computed `--since <YYYY-MM-DD>` (derived from `lookback_days`, UTC+8) so tokscale only scans the window we care about. Users can extend this to e.g. `["graph", "--clients", "zcode"]` to restrict sources; the `--since` append still happens. Verified tokscale supports `--since`/`--until`/`--week`/`--month`/`--year` (no `--days`).
- **`lookback_days`** — controls how far back the reporter asks tokscale to scan, by computing `--since = today(UTC+8) - lookback_days`. Default 90. insight also window-filters at read time, so this is mainly a tokscale performance hint.
- **`auth_token`** — OPTIONAL, reserved (see §10). Not used until insight adds auth.

Config loading: a `config.py` with a pydantic-free dataclass (no deps) or pydantic-v2 if a dependency is acceptable. **Recommendation: dataclass + manual validation**, to honor the zero-dependency goal. Validation errors → clear message, exit 2.

---

## 6. CLI Surface

```
ai-usage-reporter run [--config PATH]
    Run one collection cycle: tokscale → map → upload. Idempotent. Exit 0 on success,
    non-zero on failure (see §8).

ai-usage-reporter install [--config PATH]
    Write template config to XDG path if absent (with source_id pre-filled from hostname
    for user confirmation). Install the native OS timer (launchd plist / systemd units).
    Print the generated file paths and any manual activation commands.

ai-usage-reporter uninstall [--config PATH]
    Remove the timer unit files. Leave config + state intact.

ai-usage-reporter status [--config PATH]
    Print: last run timestamp, last result (ok/error), consecutive failures,
    count of pending replay files, tokscale binary path, target insight URL.

ai-usage-reporter --config PATH <subcommand>
    Override config path for any subcommand (useful for testing / multi-instance).
```

---

## 7. Scheduling — Native OS Timers (macOS + Linux)

`install` detects the platform and writes the matching user-domain unit (no root):

### macOS — launchd
- Path: `~/Library/LaunchAgents/ai-usage-reporter.plist`
- Job label: `ai.usage-reporter.agent`
- `StartInterval`: 900 (15 minutes).
- `EnvironmentVariables`: `TZ=Asia/Shanghai` (so tokscale buckets by UTC+8 day, regardless of the machine's local TZ).
- `ProgramArguments`: `[<reporter-bin>, "run", "--config", "<config-path>"]`.
- `StandardOutPath` / `StandardErrorPath`: `~/.local/state/ai-usage-reporter/launchd.log`.
- Loaded via `launchctl bootstrap gui/$(id -u) <plist>` (falls back to `launchctl load` on older macOS).

### Linux — systemd user
- Paths: `~/.config/systemd/user/ai-usage-reporter.service` + `ai-usage-reporter.timer`.
- Timer: `OnCalendar=*:0/15` (every 15 min), `Persistent=true` (run missed ticks after sleep), `Unit=ai-usage-reporter.service`.
- Service: `Type=oneshot`, `ExecStart=<reporter-bin> run --config <path>`, `Environment=TZ=Asia/Shanghai`.
- Enabled via `systemctl --user enable --now ai-usage-reporter.timer`.
- Requires `loginctl enable-linger <user>` if the user wants it to run while not logged in — `install` prints a hint to do this if linger is off.

**15-minute cadence** is the refresh granularity for the "today" bar; ai-plan-insight polls every 60s so the chart updates within a minute of a reporter run.

**Other platforms:** `install` errors out clearly on unsupported OS ("only macOS and Linux are supported").

---

## 8. Failure Handling & Replay

| Failure | Behavior |
|---|---|
| `tokscale` binary not on PATH / not executable | Hard error at `run` start: print install hint (`npm i -g @tokscale/cli` or repo path), exit 3. Do not write pending file. |
| `tokscale` exits non-zero | Log stderr, write `state.json` with `last_error`, exit 4. Timer retries in 15 min. |
| `tokscale` stdout not valid JSON | Same as above (exit 5). Save raw stdout to `~/.local/state/ai-usage-reporter/last-bad-stdout.txt` for debugging. |
| Empty `contributions` (no usage found) | Not an error — POST an empty `points[]`. Insight just won't update today's row. Log "no usage". Exit 0. |
| insight unreachable (connection refused / timeout) | Save payload to `~/.local/state/ai-usage-reporter/pending/<unix-ts>.json`. Exit 6. Timer retries; next successful run replays all pending files (oldest first) before the fresh run. |
| insight returns non-2xx | Same as unreachable — save to pending, exit 7. |

**Pending replay:** on each `run`, before doing the fresh cycle, the uploader drains `pending/`: POSTs each file in ascending timestamp order. Because insight UPSERTs, stacking pending files (same day reported multiple times) does NOT double-count — each just refreshes the same rows. After successful POST, the pending file is deleted. A pending file that fails to POST is left in place for the next run.

**State file** `~/.local/state/ai-usage-reporter/state.json`:
```json
{
  "last_run_at": "2026-07-02T14:38:00+08:00",
  "last_result": "ok",
  "last_error": null,
  "consecutive_failures": 0,
  "points_sent_last": 270,
  "pending_count": 0
}
```

---

## 9. Project Layout

```
ai-usage-reporter/
  pyproject.toml                  # console script: ai-usage-reporter = reporter.__main__:cli
  README.md
  reporter/
    __init__.py
    __main__.py                   # argparse → run / install / uninstall / status
    config.py                     # dataclass Config + load(path) + validation
    collector.py                  # run tokscale, capture JSON, parse
    mapper.py                     # tokscale contributions → insight points (with merge pass)
    uploader.py                   # POST to /api/usage/report; pending replay
    installer.py                  # platform dispatch: launchd vs systemd unit generation
    state.py                      # read/write state.json; pending dir management
    templates/
      config.example.json
      launchd.plist
      systemd.service
      systemd.timer
  tests/
    fixtures/
      tokscale_graph_sample.json  # captured real `tokscale graph` output
    test_mapper.py                # flatten, drop cache/cost, merge duplicates
    test_collector.py             # tokscale invocation + JSON parse (subprocess stubbed)
    test_uploader.py              # POST shaping, pending save/replay, idempotency
    test_config.py                # validation, missing source_id
    test_installer.py             # unit file generation (string comparison vs templates)
```

**Packaging:** pyproject.toml with setuptools. Console script `ai-usage-reporter`. The `ProgramArguments` in the timer unit points at this script's resolved path (computed by `install` via `sys.executable` + `-m reporter` if not installed, or the entry-point binary if installed). No compiled artifacts.

---

## 10. Security

- The reporter sends token counts and model names to `insight_url`. Default localhost. If exposed over a network, transport security is the user's responsibility (run insight behind a TLS reverse proxy).
- **`auth_token` reserved field:** if set, the uploader sends `X-Report-Key: <token>`. insight does not yet check this header; the field is present so adding auth later requires no reporter change. Until then it's inert.
- No secrets in the payload beyond usage volume. `tokscale_bin` runs locally only; no remote calls by the reporter except the one POST to insight.

---

## 11. Testing Strategy

- **`test_mapper.py`** — the core logic. Fixtured with a captured `tokscale graph` sample (stored in `tests/fixtures/`). Asserts: correct flatten, cache/reasoning/cost dropped, duplicate `(date, model_id)` across clients merged by sum, empty contributions → empty points.
- **`test_uploader.py`** — POST payload shape matches insight's `UsageReportRequest`; pending save on connection error; pending replay drains in order and deletes on success; replay is idempotent (replaying the same file twice produces the same insight state).
- **`test_collector.py`** — `subprocess.run` stubbed to return fixture JSON; asserts tokscale is invoked with configured args and `TZ` env is set in the subprocess environment.
- **`test_config.py`** — missing `source_id` → exit 2; missing `insight_url` → exit 2; unknown extra keys ignored (forward-compat).
- **`test_installer.py`** — generated plist/unit strings match templates with substitutions; platform detection (monkeypatched `sys.platform`).

No live network or live tokscale in CI — all external interactions stubbed.

---

## 12. Out of Scope (YAGNI)

- Windows support (macOS + Linux only, per requirement).
- Cost/pricing forwarding (insight doesn't display it yet).
- Cache/reasoning token forwarding.
- A long-running daemon or local HTTP server (timer + one-shot CLI only).
- Auto-updating the tokscale binary.
- Multiple insight targets (one reporter → one insight instance).
- GUI / TUI (the dashboard is insight's job).
- Automatic `source_id` rotation or multi-profile support.
