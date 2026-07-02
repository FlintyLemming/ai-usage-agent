# ai-usage-reporter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a zero-dependency Python ≥3.10 CLI that runs one collection cycle per invocation — call `tokscale graph`, flatten+merge the JSON into insight `points[]`, POST to ai-plan-insight's `/api/usage/report`, replay failed POSTs from a pending dir, and install/uninstall native OS timers (launchd on macOS, systemd user on Linux).

**Architecture:** A thin glue layer, stdlib only. `__main__.py` dispatches argparse subcommands (`run`/`install`/`uninstall`/`status`). `collector` invokes tokscale as a subprocess and parses stdout JSON; `mapper` flattens `contributions[].clients[]` to `points[]` and merges duplicates by `(date, model_id)`; `uploader` POSTs to insight with a pending-file replay loop; `installer` writes platform timer unit files; `state` persists run metadata. The same `run` command is invoked by both the human and the timer — one-shot, idempotent, no daemon.

**Tech Stack:** Python ≥3.10 (stdlib only: `json`, `subprocess`, `urllib.request`, `urllib.error`, `socket`, `os`, `sys`, `pathlib`, `argparse`, `logging`, `datetime`, `hashlib`, `shutil`, `platform`, `dataclasses`), setuptools via `pyproject.toml`, pytest for tests.

---

## Global Constraints

- **Python ≥3.10**, stdlib only — zero runtime dependencies. `pip install` is optional; the package runs as `python -m reporter`. Pytest is a dev-only dependency.
- **No external packages in production code.** Config validation uses `dataclasses` + manual checks, NOT pydantic.
- **Exit codes** (verbatim from spec §8): `0` success (incl. empty contributions); `2` config validation error; `3` tokscale binary missing; `4` tokscale exits non-zero; `5` tokscale stdout not valid JSON; `6` insight unreachable; `7` insight non-2xx response.
- **`source_id` is REQUIRED** and never auto-derived at runtime. `install` writes a template with `source_id: "<hostname>"` for the user to confirm; `run` exits 2 if unset/empty.
- **Timezone:** all date math is UTC+8 (`Asia/Shanghai`). The timer units set `TZ=Asia/Shanghai`; the reporter computes `--since` and `state.last_run_at` in UTC+8.
- **Raw `modelId` sent as-is** — no aliasing in the reporter (insight applies aliases at read time).
- **UPSERT semantics:** the reporter must merge duplicate `(date, model_id)` points across clients within a day before POST, summing input/output tokens.
- **Platforms:** macOS (launchd) and Linux (systemd user) only. `install` errors clearly on other OS.
- **No live network or live tokscale in tests** — all external interactions stubbed.
- `auth_token` is a reserved, inert config field — if set, send `X-Report-Key: <token>`, but insight does not yet check it.

**Payload contract** (verified against ai-plan-insight spec §5 and `api_schemas.py`): POST `{insight_url}/api/usage/report` with JSON body
```json
{"source_id": "...", "source_label": "...", "points": [{"date": "YYYY-MM-DD", "model_id": "...", "input_tokens": N, "output_tokens": N}]}
```
Response: any 2xx = success. Malformed JSON → 422, missing `source_id` → 400 (we never send those).

**tokscale JSON contract** (verified against `tokscale/crates/tokscale-cli/src/main.rs:4063` — `#[serde(rename_all = "camelCase")]`): `contributions[].date` is `"YYYY-MM-DD"`; `contributions[].clients[]` has `client`, `modelId`, `tokens{input,output,cacheRead,cacheWrite,reasoning}`, `cost`, `messages`. `tokscale graph` accepts `--since YYYY-MM-DD` (and `--until`, `--week`, `--month`, `--year`; no `--days`).

---

## File Structure

```
ai-usage-agent/                         # this repo (cwd)
  pyproject.toml                        # setuptools, console script ai-usage-reporter = reporter.__main__:cli
  README.md                             # brief usage
  reporter/
    __init__.py                         # version string
    __main__.py                         # argparse → cli(); dispatches run/install/uninstall/status
    config.py                           # dataclass Config + load(path) + manual validation + paths()
    collector.py                        # run tokscale, capture stdout, json.loads; exit codes 3/4/5
    mapper.py                           # flatten contributions → points + merge pass; pure function
    uploader.py                         # POST + pending replay; exit codes 6/7
    state.py                            # state.json read/write; pending dir helpers
    installer.py                        # platform dispatch; render templates with substitutions
    templates/
      config.example.json               # template config written by `install`
      launchd.plist                     # macOS LaunchAgent template
      systemd.service                   # systemd user service template
      systemd.timer                     # systemd user timer template
  tests/
    __init__.py
    conftest.py                         # fixtures: tmp XDG/state dirs, sample config
    fixtures/
      tokscale_graph_sample.json        # captured real tokscale graph output
    test_mapper.py
    test_collector.py
    test_uploader.py
    test_config.py
    test_installer.py
    test_cli.py                         # end-to-end subcommand dispatch with everything stubbed
    test_state.py
```

Each file has one responsibility. `mapper.py` is pure (no I/O) and tested in isolation first — it's the core logic. `collector`, `uploader`, `state`, `installer` wrap external boundaries and are tested with stubs. `__main__` is thin glue.

---

## Task 1: Project scaffold + pyproject + package layout

**Files:**
- Create: `pyproject.toml`
- Create: `reporter/__init__.py`
- Create: `reporter/__main__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `README.md` (replace existing 20-byte placeholder)

**Interfaces:**
- Produces: package `reporter` importable; entry point `ai-usage-reporter = "reporter.__main__:cli"` declared; `pytest` runnable.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"

[project]
name = "ai-usage-reporter"
version = "0.1.0"
description = "Thin glue: tokscale graph -> ai-plan-insight usage report. Stdlib only."
requires-python = ">=3.10"
dependencies = []  # zero runtime deps

[project.scripts]
ai-usage-reporter = "reporter.__main__:cli"

[tool.setuptools.packages.find]
include = ["reporter*"]

[tool.setuptools.package-data]
reporter = ["templates/*"]

[project.optional-dependencies]
dev = ["pytest>=7"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Write `reporter/__init__.py`**

```python
"""ai-usage-reporter: tokscale -> ai-plan-insight glue. Stdlib only."""
__version__ = "0.1.0"
```

- [ ] **Step 3: Write minimal `reporter/__main__.py` stub**

```python
"""Entry point and argparse dispatch."""
import sys


def cli() -> int:
    """Return a process exit code. Implemented in Task 8."""
    print("ai-usage-reporter: not yet implemented", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(cli())
```

- [ ] **Step 4: Write `tests/__init__.py`** (empty file, single newline)

```

```

- [ ] **Step 5: Write `tests/conftest.py`**

```python
"""Shared fixtures. No network, no live tokscale."""
import json
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def tokscale_sample() -> dict:
    """Real captured `tokscale graph` output."""
    return json.loads((FIXTURES / "tokscale_graph_sample.json").read_text())


@pytest.fixture
def sample_config_dict() -> dict:
    return {
        "source_id": "macbook-flinty",
        "source_label": "MacBook Pro",
        "insight_url": "http://127.0.0.1:8765",
        "tokscale_bin": "tokscale",
        "tokscale_args": ["graph"],
        "lookback_days": 90,
        "request_timeout_seconds": 30,
    }
```

- [ ] **Step 6: Write `README.md`**

```markdown
# ai-usage-reporter

A zero-dependency Python CLI that reports per-model daily token usage from
`totscale graph` into an [ai-plan-insight](../ai-plan-insight) instance. Runs
as a native OS timer (launchd on macOS, systemd user on Linux).

## Install

    pip install -e .[dev]
    ai-usage-reporter install     # writes config template + timer unit
    # edit ~/.config/ai-usage-reporter/config.json to set source_id
    ai-usage-reporter run

See `docs/superpowers/specs/2026-07-02-reporter-design.md` for the design.
```

- [ ] **Step 7: Verify the package imports and pytest collects**

Run: `python -c "import reporter; print(reporter.__version__)"`
Expected: `0.1.0`

Run: `python -m pytest --collect-only -q 2>&1 | tail -3`
Expected: `no tests ran` (collection succeeds, zero tests — conftest has no test fns yet) OR a pytest usage line with no error. No `ImportError`.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml reporter/__init__.py reporter/__main__.py tests/__init__.py tests/conftest.py README.md
git commit -m "feat: scaffold ai-usage-reporter package and test harness"
```

---

## Task 2: Fixture — captured tokscale graph sample

**Files:**
- Create: `tests/fixtures/tokscale_graph_sample.json`

**Interfaces:**
- Produces: a real `tokscale graph` JSON document used by every mapper/collector/uploader test. Covers: multiple days, multiple clients, duplicate `(date, model_id)` across clients, all token-breakdown fields present, empty-tokens edge case.

- [ ] **Step 1: Capture real tokscale output into the fixture**

Run:
```bash
/Users/flintylemming/Projects/tokscale/target/release/tokscale graph > /tmp/tokscale_real.json 2>/dev/null \
  || /Users/flintylemming/Projects/tokscale/target/debug/tokscale graph > /tmp/tokscale_real.json 2>/dev/null \
  || echo "FALLBACK"
python -c "import json,sys; d=json.load(open('/tmp/tokscale_real.json')); print('keys', list(d.keys())); print('days', len(d.get('contributions',[])))"
```
Expected: keys `['meta','summary','contributions']` (meta/summary may be omitted by `skip_serializing_if` if None, so accept any subset containing `contributions`), and a non-zero day count. If both tokscale invocations failed (FALLBACK), the engineer should run `cargo build` in the tokscale repo first, then retry.

- [ ] **Step 2: Synthesize a deterministic sample that covers all test cases**

Real tokscale output may not contain duplicate `(date, model_id)` across clients or an empty day, which the mapper tests need. So write a hand-crafted fixture that mirrors the real shape and adds those cases. Save as `tests/fixtures/tokscale_graph_sample.json`:

```json
{
  "meta": {"generatedAt": "2026-07-02T14:00:00+08:00", "version": "0.4.0"},
  "summary": {"totalTokens": 4160000, "totalCost": 12.34, "models": ["glm-5.2", "claude-sonnet-4-5"], "clients": ["zcode", "opencode"]},
  "contributions": [
    {
      "date": "2026-07-02",
      "totals": {"tokens": 2090000, "cost": 6.17, "messages": 120},
      "tokenBreakdown": {"input": 1500000, "output": 590000, "cacheRead": 100000, "cacheWrite": 0, "reasoning": 0},
      "clients": [
        {"client": "zcode", "modelId": "glm-5.2", "providerId": "zhipu", "tokens": {"input": 1200000, "output": 450000, "cacheRead": 80000, "cacheWrite": 0, "reasoning": 0}, "cost": 3.50, "messages": 70},
        {"client": "opencode", "modelId": "glm-5.2", "providerId": "zhipu", "tokens": {"input": 300000, "output": 140000, "cacheRead": 20000, "cacheWrite": 0, "reasoning": 0}, "cost": 1.67, "messages": 50},
        {"client": "zcode", "modelId": "claude-sonnet-4-5", "providerId": "anthropic", "tokens": {"input": 80000, "output": 30000, "cacheRead": 0, "cacheWrite": 0, "reasoning": 0}, "cost": 2.00, "messages": 20}
      ],
      "activeTimeMs": 5400000
    },
    {
      "date": "2026-07-01",
      "totals": {"tokens": 2070000, "cost": 6.17, "messages": 110},
      "tokenBreakdown": {"input": 1490000, "output": 580000, "cacheRead": 0, "cacheWrite": 0, "reasoning": 0},
      "clients": [
        {"client": "zcode", "modelId": "glm-5.2", "providerId": "zhipu", "tokens": {"input": 980000, "output": 410000, "cacheRead": 0, "cacheWrite": 0, "reasoning": 0}, "cost": 6.17, "messages": 110}
      ],
      "activeTimeMs": 5200000
    }
  ]
}
```

Note the deliberate cases: (a) two `zcode`+`opencode` entries for `glm-5.2` on `2026-07-02` that must merge to input=1500000, output=590000; (b) `cacheRead`/`cacheWrite`/`reasoning`/`cost`/`messages`/`providerId`/`activeTimeMs` present and must be **dropped** by the mapper.

- [ ] **Step 3: Verify the fixture loads**

Run: `python -c "import json; d=json.load(open('tests/fixtures/tokscale_graph_sample.json')); assert len(d['contributions'])==2; c=d['contributions'][0]['clients']; print(len(c),'clients on 2026-07-02')"`
Expected: `3 clients on 2026-07-02`

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/tokscale_graph_sample.json
git commit -m "test: add captured tokscale graph fixture with duplicate cross-client case"
```

---

## Task 3: `config.py` — dataclass Config + manual validation

**Files:**
- Create: `reporter/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `Config` dataclass with fields: `source_id: str`, `source_label: str | None`, `insight_url: str`, `tokscale_bin: str = "tokscale"`, `tokscale_args: list[str] = field(default_factory=lambda: ["graph"])`, `lookback_days: int = 90`, `request_timeout_seconds: int = 30`, `auth_token: str | None = None`.
  - `default_config_path() -> Path` — `~/.config/ai-usage-reporter/config.json` (honors `XDG_CONFIG_HOME`).
  - `default_state_dir() -> Path` — `~/.local/state/ai-usage-reporter` (honors `XDG_STATE_HOME`).
  - `load_config(path: Path) -> Config` — reads JSON, validates, raises `ConfigError` on bad input.
  - `ConfigError(Exception)` — message + `exit_code: int` (always 2).
  - `write_template_config(path: Path, hostname: str) -> Path` — writes the example with `source_id` pre-filled as `"<hostname>"` (literal angle brackets) for user confirmation.

- [ ] **Step 1: Write the failing test `tests/test_config.py`**

```python
import json
from pathlib import Path

import pytest

from reporter.config import (
    Config,
    ConfigError,
    default_config_path,
    default_state_dir,
    load_config,
    write_template_config,
)


def write_cfg(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return p


def test_load_valid_config(tmp_path, sample_config_dict):
    cfg = load_config(write_cfg(tmp_path, sample_config_dict))
    assert cfg.source_id == "macbook-flinty"
    assert cfg.source_label == "MacBook Pro"
    assert cfg.insight_url == "http://127.0.0.1:8765"
    assert cfg.tokscale_bin == "tokscale"
    assert cfg.tokscale_args == ["graph"]
    assert cfg.lookback_days == 90
    assert cfg.request_timeout_seconds == 30
    assert cfg.auth_token is None


def test_missing_source_id_exits_2(tmp_path, sample_config_dict):
    del sample_config_dict["source_id"]
    with pytest.raises(ConfigError) as exc:
        load_config(write_cfg(tmp_path, sample_config_dict))
    assert exc.value.exit_code == 2
    assert "source_id" in str(exc.value).lower()


def test_empty_source_id_exits_2(tmp_path, sample_config_dict):
    sample_config_dict["source_id"] = ""
    with pytest.raises(ConfigError) as exc:
        load_config(write_cfg(tmp_path, sample_config_dict))
    assert exc.value.exit_code == 2


def test_missing_insight_url_exits_2(tmp_path, sample_config_dict):
    del sample_config_dict["insight_url"]
    with pytest.raises(ConfigError) as exc:
        load_config(write_cfg(tmp_path, sample_config_dict))
    assert exc.value.exit_code == 2


def test_unknown_keys_ignored(tmp_path, sample_config_dict):
    sample_config_dict["future_field"] = "ignored"
    cfg = load_config(write_cfg(tmp_path, sample_config_dict))
    assert cfg.source_id == "macbook-flinty"


def test_defaults_applied(tmp_path, sample_config_dict):
    # only required fields
    minimal = {"source_id": "x", "insight_url": "http://localhost:8765"}
    cfg = load_config(write_cfg(tmp_path, minimal))
    assert cfg.source_label is None
    assert cfg.tokscale_bin == "tokscale"
    assert cfg.tokscale_args == ["graph"]
    assert cfg.lookback_days == 90
    assert cfg.request_timeout_seconds == 30


def test_bad_lookback_days_exits_2(tmp_path, sample_config_dict):
    sample_config_dict["lookback_days"] = 0
    with pytest.raises(ConfigError) as exc:
        load_config(write_cfg(tmp_path, sample_config_dict))
    assert exc.value.exit_code == 2


def test_auth_token_optional_and_kept(tmp_path, sample_config_dict):
    sample_config_dict["auth_token"] = "secret"
    cfg = load_config(write_cfg(tmp_path, sample_config_dict))
    assert cfg.auth_token == "secret"


def test_write_template_prefills_hostname(tmp_path):
    p = tmp_path / "config.json"
    out = write_template_config(p, "macbook-flinty")
    assert out == p
    data = json.loads(p.read_text())
    assert data["source_id"] == "<macbook-flinty>"
    assert data["insight_url"] == "http://127.0.0.1:8765"


def test_default_config_path_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_config_path() == tmp_path / "ai-usage-reporter" / "config.json"


def test_default_state_dir_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_state_dir() == tmp_path / "ai-usage-reporter"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v 2>&1 | tail -5`
Expected: FAIL — `ModuleNotFoundError: No module named 'reporter.config'`

- [ ] **Step 3: Write `reporter/config.py`**

```python
"""Config loading and validation. stdlib only — no pydantic."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path


class ConfigError(Exception):
    """Raised when config is missing or invalid. Maps to process exit 2."""

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class Config:
    source_id: str
    insight_url: str
    source_label: str | None = None
    tokscale_bin: str = "tokscale"
    tokscale_args: list[str] = field(default_factory=lambda: ["graph"])
    lookback_days: int = 90
    request_timeout_seconds: int = 30
    auth_token: str | None = None

    def report_url(self) -> str:
        base = self.insight_url.rstrip("/")
        return f"{base}/api/usage/report"


def default_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "ai-usage-reporter" / "config.json"


def default_state_dir() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "ai-usage-reporter"


def _require(data: dict, key: str) -> None:
    if key not in data or data[key] in (None, ""):
        raise ConfigError(f"config: missing required field '{key}'", 2)


def load_config(path: Path) -> Config:
    try:
        raw = Path(path).read_text()
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}", 2) from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConfigError(f"config: invalid JSON: {e}", 2) from None
    if not isinstance(data, dict):
        raise ConfigError("config: top-level must be an object", 2)

    _require(data, "source_id")
    _require(data, "insight_url")

    lookback = data.get("lookback_days", 90)
    if not isinstance(lookback, int) or lookback < 1:
        raise ConfigError("config: 'lookback_days' must be a positive integer", 2)

    timeout = data.get("request_timeout_seconds", 30)
    if not isinstance(timeout, int) or timeout < 1:
        raise ConfigError("config: 'request_timeout_seconds' must be a positive integer", 2)

    args = data.get("tokscale_args", ["graph"])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ConfigError("config: 'tokscale_args' must be a list of strings", 2)

    return Config(
        source_id=str(data["source_id"]),
        insight_url=str(data["insight_url"]),
        source_label=data.get("source_label"),
        tokscale_bin=str(data.get("tokscale_bin", "tokscale")),
        tokscale_args=list(args),
        lookback_days=lookback,
        request_timeout_seconds=timeout,
        auth_token=data.get("auth_token"),
    )


def write_template_config(path: Path, hostname: str) -> Path:
    """Write the example config with source_id pre-filled for confirmation."""
    template = {
        "source_id": f"<{hostname}>",
        "source_label": None,
        "insight_url": "http://127.0.0.1:8765",
        "tokscale_bin": "tokscale",
        "tokscale_args": ["graph"],
        "lookback_days": 90,
        "request_timeout_seconds": 30,
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(template, indent=2) + "\n")
    return p
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v 2>&1 | tail -15`
Expected: PASS — all tests green.

- [ ] **Step 5: Commit**

```bash
git add reporter/config.py tests/test_config.py
git commit -m "feat: config dataclass with manual validation (exit 2 on bad config)"
```

---

## Task 4: `mapper.py` — flatten + merge (pure, no I/O)

**Files:**
- Create: `reporter/mapper.py`
- Create: `tests/test_mapper.py`

**Interfaces:**
- Consumes: a `dict` parsed from `tokscale graph` stdout (shape per spec §3 / fixture).
- Produces: `map_points(tokscale_json: dict) -> list[dict]` returning insight `points[]` — each `{"date": str, "model_id": str, "input_tokens": int, "output_tokens": int}`, with duplicate `(date, model_id)` merged by summing, and all non-input/output fields dropped. Empty `contributions` → `[]`.

- [ ] **Step 1: Write the failing test `tests/test_mapper.py`**

```python
from reporter.mapper import map_points


def test_flatten_three_clients_one_day(tokscale_sample):
    points = map_points(tokscale_sample)
    # 2026-07-02: glm-5.2 (merged from zcode+opencode), claude-sonnet-4-5
    # 2026-07-01: glm-5.2
    by_key = {(p["date"], p["model_id"]): p for p in points}
    assert set(by_key) == {
        ("2026-07-02", "glm-5.2"),
        ("2026-07-02", "claude-sonnet-4-5"),
        ("2026-07-01", "glm-5.2"),
    }


def test_merge_duplicate_across_clients(tokscale_sample):
    points = map_points(tokscale_sample)
    p = next(p for p in points if p["date"] == "2026-07-02" and p["model_id"] == "glm-5.2")
    # zcode input 1_200_000 + opencode input 300_000 = 1_500_000
    # zcode output 450_000 + opencode output 140_000 = 590_000
    assert p["input_tokens"] == 1_500_000
    assert p["output_tokens"] == 590_000


def test_drops_cache_reasoning_cost_messages(tokscale_sample):
    points = map_points(tokscale_sample)
    for p in points:
        assert set(p.keys()) == {"date", "model_id", "input_tokens", "output_tokens"}


def test_raw_model_id_no_aliasing(tokscale_sample):
    points = map_points(tokscale_sample)
    ids = {p["model_id"] for p in points}
    assert "claude-sonnet-4-5" in ids
    assert "glm-5.2" in ids


def test_empty_contributions():
    assert map_points({"contributions": []}) == []
    assert map_points({}) == []


def test_missing_token_fields_default_zero():
    data = {"contributions": [{"date": "2026-07-02", "clients": [
        {"client": "zcode", "modelId": "glm-5.2", "tokens": {}},
    ]}]}
    pts = map_points(data)
    assert pts == [{"date": "2026-07-02", "model_id": "glm-5.2", "input_tokens": 0, "output_tokens": 0}]


def test_missing_input_key_uses_get_default():
    data = {"contributions": [{"date": "2026-07-02", "clients": [
        {"client": "zcode", "modelId": "glm-5.2", "tokens": {"output": 100}},
    ]}]}
    pts = map_points(data)
    assert pts[0]["input_tokens"] == 0
    assert pts[0]["output_tokens"] == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mapper.py -v 2>&1 | tail -5`
Expected: FAIL — `ModuleNotFoundError: No module named 'reporter.mapper'`

- [ ] **Step 3: Write `reporter/mapper.py`**

```python
"""Flatten tokscale contributions into insight points (pure, no I/O).

Drops cache/cacheWrite/reasoning/cost/messages/client/providerId/activeTimeMs.
Merges duplicate (date, model_id) across clients by summing input/output.
"""
from __future__ import annotations


def map_points(tokscale_json: dict) -> list[dict]:
    """Return insight points[] from a parsed `tokscale graph` document.

    Each point: {"date","model_id","input_tokens","output_tokens"}.
    Duplicate (date, model_id) across clients within a day are merged by sum.
    """
    contributions = tokscale_json.get("contributions") or []
    merged: dict[tuple[str, str], dict] = {}
    for day in contributions:
        date = day.get("date")
        for src in day.get("clients") or []:
            model_id = src.get("modelId")
            tokens = src.get("tokens") or {}
            input_tokens = int(tokens.get("input", 0) or 0)
            output_tokens = int(tokens.get("output", 0) or 0)
            key = (date, model_id)
            if key in merged:
                merged[key]["input_tokens"] += input_tokens
                merged[key]["output_tokens"] += output_tokens
            else:
                merged[key] = {
                    "date": date,
                    "model_id": model_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }
    return list(merged.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mapper.py -v 2>&1 | tail -10`
Expected: PASS — all 7 tests green.

- [ ] **Step 5: Commit**

```bash
git add reporter/mapper.py tests/test_mapper.py
git commit -m "feat: mapper flattens tokscale contributions and merges by (date, model_id)"
```

---

## Task 5: `collector.py` — run tokscale subprocess and parse JSON

**Files:**
- Create: `reporter/collector.py`
- Create: `tests/test_collector.py`

**Interfaces:**
- Consumes: `reporter.config.Config` (uses `tokscale_bin`, `tokscale_args`, `lookback_days`).
- Produces:
  - `CollectorError(Exception)` with `.exit_code` (3 binary missing, 4 tokscale non-zero, 5 bad JSON).
  - `collect(config: Config, *, runner=None) -> dict` — resolves the tokscale binary via `shutil.which`, builds argv = `tokscale_args + ["--since", "<YYYY-MM-DD>"]` where the date is `today(UTC+8) - lookback_days`, runs the subprocess with `TZ=Asia/Shanghai` in its environment, captures stdout, returns `json.loads(stdout)`. Raises `CollectorError` on the three failure modes.
  - `runner` is an optional injection point `Callable[[list[str], dict[str,str]], CompletedProcess]` defaulting to a thin wrapper over `subprocess.run`; tests stub it.
  - `since_date(lookback_days: int, *, now_utc8: datetime | None = None) -> str` — pure helper returning `"YYYY-MM-DD"`; tests call it directly.

- [ ] **Step 1: Write the failing test `tests/test_collector.py`**

```python
import json
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from reporter.collector import CollectorError, collect, since_date
from reporter.config import Config


UTC8 = timezone(timedelta(hours=8))


def make_cfg(**over) -> Config:
    base = dict(
        source_id="m", insight_url="http://localhost:8765",
        tokscale_bin="tokscale", tokscale_args=["graph"], lookback_days=90,
    )
    base.update(over)
    return Config(**base)


def fake_runner(stdout: bytes = b'{"contributions":[]}', returncode: int = 0, stderr: bytes = b""):
    calls = []

    def run(argv, env):
        calls.append((argv, env))
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    return run, calls


def test_collect_invokes_tokscale_with_since(tmp_path, tokscale_sample):
    run, calls = fake_runner(json.dumps(tokscale_sample).encode())
    cfg = make_cfg()
    data = collect(cfg, runner=run)
    argv = calls[0][0]
    assert argv[0].endswith("tokscale") or argv[0] == "tokscale"
    assert "--since" in argv
    assert "graph" in argv


def test_collect_sets_tz_shanghai_in_env(tmp_path):
    run, calls = fake_runner(b'{"contributions":[]}')
    collect(make_cfg(), runner=run)
    env = calls[0][1]
    assert env.get("TZ") == "Asia/Shanghai"


def test_collect_parses_json(tokscale_sample):
    run, _ = fake_runner(json.dumps(tokscale_sample).encode())
    data = collect(make_cfg(), runner=run)
    assert "contributions" in data
    assert len(data["contributions"]) == 2


def test_binary_missing_exits_3(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(CollectorError) as exc:
        collect(make_cfg(tokscale_bin="nope-tokscale"))
    assert exc.value.exit_code == 3


def test_binary_not_executable_exits_3(tmp_path, monkeypatch):
    fake = tmp_path / "tokscale"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o644)  # not executable
    monkeypatch.setattr("shutil.which", lambda name: str(fake) if name == "tokscale" else None)
    with pytest.raises(CollectorError) as exc:
        collect(make_cfg(tokscale_bin=str(fake)))
    assert exc.value.exit_code == 3


def test_tokscale_nonzero_exits_4():
    run, _ = fake_runner(b"oops", returncode=1, stderr=b"error: boom")
    with pytest.raises(CollectorError) as exc:
        collect(make_cfg(), runner=run)
    assert exc.value.exit_code == 4
    assert "boom" in str(exc.value)


def test_bad_json_exits_5(tmp_path):
    run, _ = fake_runner(b"not json at all")
    with pytest.raises(CollectorError) as exc:
        collect(make_cfg(), runner=run)
    assert exc.value.exit_code == 5


def test_since_date_default():
    # 2026-07-02 minus 90 days = 2026-04-03
    now = datetime(2026, 7, 2, 15, 0, tzinfo=UTC8)
    assert since_date(90, now_utc8=now) == "2026-04-03"


def test_since_date_zero_lookback():
    now = datetime(2026, 7, 2, 0, 30, tzinfo=UTC8)
    # midnight UTC+8 boundary -> 2026-07-02
    assert since_date(0, now_utc8=now) == "2026-07-02"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_collector.py -v 2>&1 | tail -5`
Expected: FAIL — `ModuleNotFoundError: No module named 'reporter.collector'`

- [ ] **Step 3: Write `reporter/collector.py`**

```python
"""Run `tokscale graph` and parse stdout JSON. Exit codes 3/4/5."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .config import Config

log = logging.getLogger("reporter.collector")

UTC8 = timezone(timedelta(hours=8))


class CollectorError(Exception):
    """tokscale invocation failure. exit_code: 3/4/5."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def since_date(lookback_days: int, *, now_utc8: datetime | None = None) -> str:
    """Return 'YYYY-MM-DD' = today(UTC+8) - lookback_days."""
    now = now_utc8 or datetime.now(UTC8)
    return (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d")


def _resolve_binary(name_or_path: str) -> str:
    """Return an executable path or raise CollectorError(exit 3)."""
    if os.sep in name_or_path or (os.altsep and os.altsep in name_or_path):
        # explicit path
        p = Path(name_or_path)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
        raise CollectorError(
            f"tokscale binary not executable: {name_or_path} "
            f"(install: `npm i -g @tokscale/cli` or build from the tokscale repo)",
            3,
        )
    resolved = shutil.which(name_or_path)
    if resolved:
        return resolved
    raise CollectorError(
        f"tokscale binary '{name_or_path}' not found on PATH "
        f"(install: `npm i -g @tokscale/cli` or build from the tokscale repo)",
        3,
    )


def _default_runner(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, env=env)


def collect(config: Config, *, runner: Callable | None = None) -> dict:
    """Run tokscale, return parsed JSON. Raises CollectorError on failure."""
    bin_path = _resolve_binary(config.tokscale_bin)
    argv = [bin_path, *config.tokscale_args, "--since", since_date(config.lookback_days)]
    env = dict(os.environ)
    env["TZ"] = "Asia/Shanghai"
    log.info("running tokscale: %s", argv)
    run = runner or _default_runner
    proc = run(argv, env)
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace") if proc.stderr else ""
        raise CollectorError(f"tokscale exited {proc.returncode}: {stderr.strip()}", 4)
    stdout = proc.stdout or b""
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        raise CollectorError(f"tokscale stdout not valid JSON: {e}", 5) from None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_collector.py -v 2>&1 | tail -12`
Expected: PASS — all 9 tests green.

- [ ] **Step 5: Commit**

```bash
git add reporter/collector.py tests/test_collector.py
git commit -m "feat: collector runs tokscale with --since (TZ=Asia/Shanghai), exit 3/4/5"
```

---

## Task 6: `state.py` — state.json + pending dir management

**Files:**
- Create: `reporter/state.py`
- Create: `tests/test_state.py`

**Interfaces:**
- Consumes: a state directory `Path` (default `default_state_dir()`).
- Produces:
  - `read_state(state_dir: Path) -> dict` — returns state dict, defaulting to `{"last_run_at": None, "last_result": None, "last_error": None, "consecutive_failures": 0, "points_sent_last": 0, "pending_count": 0}` if file missing.
  - `write_state(state_dir: Path, **fields) -> dict` — merges fields and writes `state.json` (atomic temp+rename), recomputes `pending_count` from the pending dir, returns the new state dict.
  - `pending_dir(state_dir: Path) -> Path` — `state_dir / "pending"`, created on demand.
  - `save_pending(state_dir: Path, payload: dict) -> Path` — writes `<unix-ts>.json` into pending, returns the path. The timestamp is the kwarg `ts: float` (injectable for tests); default behavior in production is supplied by the caller — `save_pending` itself takes `ts` explicitly (no `time.time()` call inside, to stay deterministic and testable).
  - `list_pending(state_dir: Path) -> list[Path]` — pending files sorted by filename ascending (oldest first).
  - `delete_pending(path: Path) -> None` — removes a file, ignoring FileNotFound.

- [ ] **Step 1: Write the failing test `tests/test_state.py`**

```python
import json
from pathlib import Path

from reporter.state import (
    delete_pending, list_pending, pending_dir, read_state, save_pending, write_state,
)


def test_read_state_missing_returns_defaults(tmp_path):
    s = read_state(tmp_path)
    assert s == {
        "last_run_at": None, "last_result": None, "last_error": None,
        "consecutive_failures": 0, "points_sent_last": 0, "pending_count": 0,
    }


def test_write_state_merges_and_counts_pending(tmp_path):
    save_pending(tmp_path, {"points": []}, ts=100.0)
    save_pending(tmp_path, {"points": []}, ts=50.0)
    s = write_state(tmp_path, last_run_at="2026-07-02T14:00:00+08:00", last_result="ok")
    assert s["last_result"] == "ok"
    assert s["pending_count"] == 2


def test_save_pending_uses_ts_in_filename(tmp_path):
    p = save_pending(tmp_path, {"points": [{"date": "x"}]}, ts=1234567.89)
    assert p.parent == tmp_path / "pending"
    assert p.name.startswith("1234567")
    assert json.loads(p.read_text())["points"][0]["date"] == "x"


def test_list_pending_sorted_oldest_first(tmp_path):
    save_pending(tmp_path, {}, ts=200.0)
    save_pending(tmp_path, {}, ts=10.0)
    save_pending(tmp_path, {}, ts=100.0)
    names = [p.name for p in list_pending(tmp_path)]
    assert names == sorted(names)  # ascending


def test_delete_pending_idempotent(tmp_path):
    p = save_pending(tmp_path, {}, ts=1.0)
    delete_pending(p)
    delete_pending(p)  # no error
    assert not p.exists()


def test_write_state_atomic(tmp_path):
    write_state(tmp_path, last_result="ok")
    assert (tmp_path / "state.json").exists()
    # no leftover temp files
    assert not list((tmp_path).glob("*.tmp"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_state.py -v 2>&1 | tail -5`
Expected: FAIL — `ModuleNotFoundError: No module named 'reporter.state'`

- [ ] **Step 3: Write `reporter/state.py`**

```python
"""state.json + pending directory management."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

DEFAULT_STATE = {
    "last_run_at": None,
    "last_result": None,
    "last_error": None,
    "consecutive_failures": 0,
    "points_sent_last": 0,
    "pending_count": 0,
}


def pending_dir(state_dir: Path) -> Path:
    d = Path(state_dir) / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_state(state_dir: Path) -> dict:
    f = Path(state_dir) / "state.json"
    if not f.exists():
        return dict(DEFAULT_STATE)
    try:
        data = json.loads(f.read_text())
    except json.JSONDecodeError:
        return dict(DEFAULT_STATE)
    merged = dict(DEFAULT_STATE)
    merged.update(data)
    merged["pending_count"] = len(list_pending(state_dir))
    return merged


def write_state(state_dir: Path, **fields) -> dict:
    state = read_state(state_dir)
    state.update(fields)
    state["pending_count"] = len(list_pending(state_dir))
    out = Path(state_dir) / "state.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # atomic write: temp in same dir, then rename
    fd, tmppath = tempfile.mkstemp(dir=out.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmppath, out)
    except Exception:
        try:
            os.unlink(tmppath)
        except OSError:
            pass
        raise
    return state


def save_pending(state_dir: Path, payload: dict, *, ts: float) -> Path:
    """Write payload to pending/<ts>.json. ts is supplied by the caller."""
    d = pending_dir(state_dir)
    # filename uses the integer ts to keep lexical sort == chronological
    name = f"{int(ts):010d}.json"
    p = d / name
    p.write_text(json.dumps(payload))
    return p


def list_pending(state_dir: Path) -> list[Path]:
    d = Path(state_dir) / "pending"
    if not d.exists():
        return []
    return sorted(d.glob("*.json"))


def delete_pending(path: Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_state.py -v 2>&1 | tail -10`
Expected: PASS — all 6 tests green.

- [ ] **Step 5: Commit**

```bash
git add reporter/state.py tests/test_state.py
git commit -m "feat: state.json + pending dir management (atomic writes)"
```

---

## Task 7: `uploader.py` — POST + pending replay

**Files:**
- Create: `reporter/uploader.py`
- Create: `tests/test_uploader.py`

**Interfaces:**
- Consumes: `reporter.config.Config` (`report_url()`, `source_id`, `source_label`, `auth_token`, `request_timeout_seconds`), a state dir `Path`, and an injectable `poster` callable.
- Produces:
  - `UploaderError(Exception)` with `.exit_code` (6 connection/timeout, 7 non-2xx).
  - `post_payload(config, points, *, poster) -> None` — builds `{"source_id", "source_label", "points"}` JSON, POSTs to `config.report_url()`, raises `UploaderError(7)` on non-2xx, `UploaderError(6)` on `URLError`/`socket.timeout`. `poster` is `Callable[[str, bytes, dict[str,str], int], (int, bytes|None)]` returning `(status_code, body)`; default wraps `urllib.request.urlopen`.
  - `upload(config, points, state_dir, *, poster=None, now_ts: float | None = None) -> int` — the main entry. Drains pending first (oldest first, delete on success, leave on failure), then posts the fresh payload. Returns the number of points sent in the fresh POST. On fresh-POST failure, saves the fresh payload to pending and re-raises. `now_ts` is the timestamp for any new pending file (injectable for tests; production passes `time.time()`).

- [ ] **Step 1: Write the failing test `tests/test_uploader.py`**

```python
import json
import socket
import urllib.error
from urllib.request import urlopen

import pytest

from reporter.config import Config
from reporter.state import list_pending, save_pending
from reporter.uploader import UploaderError, post_payload, upload


def cfg(**over) -> Config:
    base = dict(source_id="m", insight_url="http://localhost:8765",
                request_timeout_seconds=30)
    base.update(over)
    return Config(**base)


def fake_poster(status=200):
    """Returns (runner, received) where received is a list of (url, body)."""
    received = []

    def run(url, body, headers, timeout):
        received.append((url, json.loads(body), headers, timeout))
        return status, b""

    return run, received


def test_post_payload_shape():
    run, received = fake_poster()
    c = cfg()
    post_payload(c, [{"date": "2026-07-02", "model_id": "glm-5.2",
                      "input_tokens": 10, "output_tokens": 5}], poster=run)
    url, body, headers, timeout = received[0]
    assert url == "http://127.0.0.1:8765/api/usage/report"
    assert body["source_id"] == "m"
    assert body["source_label"] is None
    assert body["points"] == [{"date": "2026-07-02", "model_id": "glm-5.2",
                                "input_tokens": 10, "output_tokens": 5}]
    assert timeout == 30


def test_post_payload_sends_auth_header_when_set():
    run, received = fake_poster()
    c = cfg(auth_token="sekret")
    post_payload(c, [], poster=run)
    assert received[0][2].get("X-Report-Key") == "sekret"


def test_post_payload_no_auth_header_when_unset():
    run, received = fake_poster()
    post_payload(cfg(), [], poster=run)
    assert "X-Report-Key" not in received[0][2]


def test_non_2xx_exits_7():
    run, _ = fake_poster(status=500)
    with pytest.raises(UploaderError) as exc:
        post_payload(cfg(), [], poster=run)
    assert exc.value.exit_code == 7


def test_url_error_exits_6():
    def run(url, body, headers, timeout):
        raise urllib.error.URLError("connection refused")
    with pytest.raises(UploaderError) as exc:
        post_payload(cfg(), [], poster=run)
    assert exc.value.exit_code == 6


def test_socket_timeout_exits_6():
    def run(url, body, headers, timeout):
        raise socket.timeout("timed out")
    with pytest.raises(UploaderError) as exc:
        post_payload(cfg(), [], poster=run)
    assert exc.value.exit_code == 6


def test_upload_drains_pending_first(tmp_path):
    # pre-existing pending file from a prior failed run
    save_pending(tmp_path, {"source_id": "m", "source_label": None,
                            "points": [{"date": "2026-07-01", "model_id": "glm-5.2",
                                        "input_tokens": 1, "output_tokens": 1}]}, ts=10.0)
    run, received = fake_poster()
    upload(cfg(), [], tmp_path, poster=run, now_ts=100.0)
    # pending posted first, then fresh (empty points) posted second
    assert len(received) == 2
    assert received[0][1]["points"][0]["date"] == "2026-07-01"
    assert received[1][1]["points"] == []
    # pending file deleted after success
    assert list_pending(tmp_path) == []


def test_upload_leaves_pending_on_failure(tmp_path):
    save_pending(tmp_path, {"source_id": "m", "source_label": None, "points": []}, ts=10.0)
    run, _ = fake_poster(status=500)
    with pytest.raises(UploaderError):
        upload(cfg(), [], tmp_path, poster=run, now_ts=100.0)
    # the old pending file stays; the fresh payload also gets saved
    pend = list_pending(tmp_path)
    assert len(pend) == 2
    # oldest is the original (ts=10), newest is the fresh (ts=100)
    names = sorted(p.name for p in pend)
    assert names[0].startswith("0000000010")
    assert names[1].startswith("0000000100")


def test_upload_saves_fresh_on_failure(tmp_path):
    run, _ = fake_poster(status=503)
    with pytest.raises(UploaderError):
        upload(cfg(), [{"date": "2026-07-02", "model_id": "glm-5.2",
                        "input_tokens": 9, "output_tokens": 9}],
               tmp_path, poster=run, now_ts=200.0)
    pend = list_pending(tmp_path)
    assert len(pend) == 1
    body = json.loads(pend[0].read_text())
    assert body["points"][0]["input_tokens"] == 9


def test_upload_returns_points_sent(tmp_path):
    run, _ = fake_poster()
    pts = [{"date": "2026-07-02", "model_id": "glm-5.2", "input_tokens": 1, "output_tokens": 1}]
    n = upload(cfg(), pts, tmp_path, poster=run, now_ts=1.0)
    assert n == 1


def test_upload_empty_pending_no_drain(tmp_path):
    run, received = fake_poster()
    upload(cfg(), [], tmp_path, poster=run, now_ts=1.0)
    assert len(received) == 1  # only the fresh post
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_uploader.py -v 2>&1 | tail -5`
Expected: FAIL — `ModuleNotFoundError: No module named 'reporter.uploader'`

- [ ] **Step 3: Write `reporter/uploader.py`**

```python
"""POST points to insight + replay pending files. Exit codes 6/7."""
from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .config import Config
from .state import delete_pending, list_pending, save_pending

log = logging.getLogger("reporter.uploader")


class UploaderError(Exception):
    """Upload failure. exit_code: 6 (unreachable) / 7 (non-2xx)."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _default_poster(url: str, body: bytes, headers: dict[str, str], timeout: int):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, None
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, socket.timeout) as e:
        raise  # re-raised; caller maps to exit 6


def post_payload(config: Config, points: list[dict], *, poster: Callable) -> None:
    """POST one payload. Raises UploaderError(6/7)."""
    body = json.dumps({
        "source_id": config.source_id,
        "source_label": config.source_label,
        "points": points,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if config.auth_token:
        headers["X-Report-Key"] = config.auth_token
    try:
        status, _ = poster(config.report_url(), body, headers, config.request_timeout_seconds)
    except (urllib.error.URLError, socket.timeout) as e:
        raise UploaderError(f"insight unreachable: {e}", 6) from None
    if not (200 <= status < 300):
        raise UploaderError(f"insight returned HTTP {status}", 7)


def upload(config: Config, points: list[dict], state_dir: Path,
           *, poster: Callable | None = None, now_ts: float | None = None) -> int:
    """Drain pending (oldest first), then POST the fresh payload.

    On fresh-POST failure, save the fresh payload to pending and re-raise.
    Returns the number of points in the fresh payload.
    """
    post = poster or _default_poster

    # 1. drain pending
    for p in list_pending(state_dir):
        try:
            payload = json.loads(p.read_text())
        except json.JSONDecodeError:
            log.warning("skipping corrupt pending file %s", p)
            delete_pending(p)
            continue
        try:
            post_payload(config, payload["points"], poster=post)
        except UploaderError as e:
            log.warning("pending replay failed for %s: %s", p, e)
            raise  # leave file in place, surface the error
        delete_pending(p)
        log.info("replayed pending %s", p)

    # 2. fresh post
    try:
        post_payload(config, points, poster=post)
    except UploaderError:
        if now_ts is not None:
            save_pending(state_dir, {
                "source_id": config.source_id,
                "source_label": config.source_label,
                "points": points,
            }, ts=now_ts)
        raise
    return len(points)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_uploader.py -v 2>&1 | tail -15`
Expected: PASS — all 12 tests green.

- [ ] **Step 5: Commit**

```bash
git add reporter/uploader.py tests/test_uploader.py
git commit -m "feat: uploader POSTs to insight, drains pending first (exit 6/7)"
```

---

## Task 8: `installer.py` — platform dispatch + unit file templates

**Files:**
- Create: `reporter/templates/config.example.json`
- Create: `reporter/templates/launchd.plist`
- Create: `reporter/templates/systemd.service`
- Create: `reporter/templates/systemd.timer`
- Create: `reporter/installer.py`
- Create: `tests/test_installer.py`

**Interfaces:**
- Produces:
  - `install(config_path: Path, *, platform_name: str | None = None, hostname: str | None = None, reporter_cmd: list[str] | None = None, state_dir: Path | None = None) -> dict` — writes template config (if absent) with `source_id="<hostname>"`, writes the platform unit files with substitutions, loads/activates them, returns a dict of generated paths + hints. Raises `InstallerError` on unsupported platform.
  - `uninstall(*, platform_name: str | None = None) -> None` — removes the unit files (leaves config + state).
  - `InstallerError(Exception)` exit code 2.
  - Substitution variables in templates: `${REPORTER_CMD_ARGS}` (a JSON-style or space-joined argv — we use a Python-rendered plist/ini, see templates), `${CONFIG_PATH}`, `${STATE_LOG_PATH}`.

- [ ] **Step 1: Write the template files**

`reporter/templates/config.example.json`:
```json
{
  "source_id": "<hostname>",
  "source_label": null,
  "insight_url": "http://127.0.0.1:8765",
  "tokscale_bin": "tokscale",
  "tokscale_args": ["graph"],
  "lookback_days": 90,
  "request_timeout_seconds": 30
}
```

`reporter/templates/launchd.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>ai.usage-reporter.agent</string>
    <key>ProgramArguments</key>
    <array>
        <!-- REPORTER_CMD -->
    </array>
    <key>StartInterval</key>
    <integer>900</integer>
    <key>EnvironmentVariables</key>
    <dict>
        <key>TZ</key>
        <string>Asia/Shanghai</string>
    </dict>
    <key>StandardOutPath</key>
    <string>STATE_LOG_PATH</string>
    <key>StandardErrorPath</key>
    <string>STATE_LOG_PATH</string>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
```

`reporter/templates/systemd.service`:
```ini
[Unit]
Description=ai-usage-reporter one-shot collection cycle

[Service]
Type=oneshot
Environment=TZ=Asia/Shanghai
ExecStart=REPORTER_CMD

[Install]
WantedBy=default.target
```

`reporter/templates/systemd.timer`:
```ini
[Unit]
Description=Run ai-usage-reporter every 15 minutes

[Timer]
OnCalendar=*:0/15
Persistent=true
Unit=ai-usage-reporter.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 2: Write the failing test `tests/test_installer.py`**

```python
import json
from pathlib import Path

import pytest

from reporter.installer import InstallerError, install, uninstall


def test_install_macos_writes_plist_and_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("socket.gethostname", lambda: "macbook-flinty")

    cfg_path = tmp_path / "xdg" / "ai-usage-reporter" / "config.json"
    result = install(cfg_path, reporter_cmd=["/usr/local/bin/ai-usage-reporter", "run", "--config", str(cfg_path)])

    plist = home / "Library" / "LaunchAgents" / "ai-usage-reporter.plist"
    assert plist.exists()
    text = plist.read_text()
    assert "ai.usage-reporter.agent" in text
    assert "<integer>900</integer>" in text
    assert "Asia/Shanghai" in text
    assert "/usr/local/bin/ai-usage-reporter" in text
    assert "run" in text and "--config" in text and str(cfg_path) in text

    # config template written with hostname placeholder
    data = json.loads(cfg_path.read_text())
    assert data["source_id"] == "<macbook-flinty>"


def test_install_linux_writes_systemd_units(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("socket.gethostname", lambda: "linuxbox")
    (tmp_path / "home").mkdir()

    cfg_path = tmp_path / "xdg" / "ai-usage-reporter" / "config.json"
    install(cfg_path, reporter_cmd=["/usr/bin/ai-usage-reporter", "run", "--config", str(cfg_path)])

    units = tmp_path / "home" / ".config" / "systemd" / "user"
    svc = (units / "ai-usage-reporter.service").read_text()
    tmr = (units / "ai-usage-reporter.timer").read_text()
    assert "Type=oneshot" in svc
    assert "ExecStart=/usr/bin/ai-usage-reporter run --config" in svc
    assert "OnCalendar=*:0/15" in tmr
    assert "Persistent=true" in tmr


def test_install_unsupported_platform_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Windows")
    with pytest.raises(InstallerError):
        install(tmp_path / "c.json", reporter_cmd=["x"])


def test_install_does_not_overwrite_existing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("socket.gethostname", lambda: "mb")

    cfg_path = tmp_path / "xdg" / "ai-usage-reporter" / "config.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps({"source_id": "already-set", "insight_url": "http://x"}))
    install(cfg_path, reporter_cmd=["x", "run", "--config", str(cfg_path)])
    assert json.loads(cfg_path.read_text())["source_id"] == "already-set"


def test_uninstall_macos_removes_plist(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    plist = home / "Library" / "LaunchAgents" / "ai-usage-reporter.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("dummy")
    uninstall()
    assert not plist.exists()


def test_uninstall_linux_removes_units(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr("platform.system", lambda: "Linux")
    units = tmp_path / "home" / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    (units / "ai-usage-reporter.service").write_text("x")
    (units / "ai-usage-reporter.timer").write_text("x")
    uninstall()
    assert not (units / "ai-usage-reporter.service").exists()
    assert not (units / "ai-usage-reporter.timer").exists()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_installer.py -v 2>&1 | tail -5`
Expected: FAIL — `ModuleNotFoundError: No module named 'reporter.installer'`

- [ ] **Step 4: Write `reporter/installer.py`**

```python
"""Platform timer installation: launchd (macOS) + systemd user (Linux)."""
from __future__ import annotations

import json
import logging
import platform
import shutil
import socket
import subprocess
from pathlib import Path

from .config import default_state_dir

log = logging.getLogger("reporter.installer")

LAUNCHD_LABEL = "ai.usage-reporter.agent"
START_INTERVAL = 900  # 15 minutes

TEMPLATES = Path(__file__).parent / "templates"


class InstallerError(Exception):
    pass


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "ai-usage-reporter.plist"


def _systemd_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _state_log_path() -> Path:
    return default_state_dir() / "launchd.log"


def _render_plist(reporter_cmd: list[str]) -> str:
    args_xml = "\n".join(f"        <string>{a}</string>" for a in reporter_cmd)
    log_path = _state_log_path()
    text = (TEMPLATES / "launchd.plist").read_text()
    text = text.replace("<!-- REPORTER_CMD -->", args_xml)
    text = text.replace("STATE_LOG_PATH", str(log_path))
    return text


def _render_service(reporter_cmd: list[str]) -> str:
    text = (TEMPLATES / "systemd.service").read_text()
    text = text.replace("REPORTER_CMD", " ".join(reporter_cmd))
    return text


def _write_template_config(config_path: Path, hostname: str) -> None:
    p = Path(config_path)
    if p.exists():
        log.info("config already exists, leaving untouched: %s", p)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads((TEMPLATES / "config.example.json").read_text())
    data["source_id"] = f"<{hostname}>"
    p.write_text(json.dumps(data, indent=2) + "\n")
    log.info("wrote template config: %s (confirm/edit source_id)", p)


def install(config_path: Path, *,
            platform_name: str | None = None,
            hostname: str | None = None,
            reporter_cmd: list[str] | None = None,
            state_dir: Path | None = None) -> dict:
    """Write template config (if absent) + platform unit files; activate.

    Returns dict with generated paths and any manual hint commands.
    """
    plat = (platform_name or platform.system()).lower()
    host = hostname or socket.gethostname()
    if reporter_cmd is None:
        raise InstallerError("reporter_cmd is required (the resolved invocation)")

    _write_template_config(config_path, host)
    result: dict = {"config_path": str(config_path), "platform": plat, "hints": []}

    if plat == "darwin":
        plist_path = _launchd_plist_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(_render_plist(reporter_cmd))
        result["plist_path"] = str(plist_path)
        result["hints"].append(
            f"launchctl bootstrap gui/$(id -u) {plist_path}  "
            f"(fallback: launchctl load {plist_path})"
        )
        log.info("wrote launchd plist: %s", plist_path)
    elif plat == "linux":
        units = _systemd_unit_dir()
        units.mkdir(parents=True, exist_ok=True)
        (units / "ai-usage-reporter.service").write_text(_render_service(reporter_cmd))
        (units / "ai-usage-reporter.timer").write_text(
            (TEMPLATES / "systemd.timer").read_text())
        result["service_path"] = str(units / "ai-usage-reporter.service")
        result["timer_path"] = str(units / "ai-usage-reporter.timer")
        result["hints"].append("systemctl --user enable --now ai-usage-reporter.timer")
        result["hints"].append("loginctl enable-linger $USER  (if you want it to run while logged out)")
        log.info("wrote systemd units: %s", units)
    else:
        raise InstallerError(f"only macOS and Linux are supported (got {plat})")

    return result


def uninstall(*, platform_name: str | None = None) -> None:
    plat = (platform_name or platform.system()).lower()
    if plat == "darwin":
        plist = _launchd_plist_path()
        if plist.exists():
            shutil.rmtree(plist.parent / "ai-usage-reporter.plist", ignore_errors=True)
            try:
                plist.unlink()
            except FileNotFoundError:
                pass
        log.info("removed launchd plist: %s", plist)
    elif plat == "linux":
        units = _systemd_unit_dir()
        for name in ("ai-usage-reporter.service", "ai-usage-reporter.timer"):
            try:
                (units / name).unlink()
            except FileNotFoundError:
                pass
        log.info("removed systemd units: %s", units)
    else:
        raise InstallerError(f"only macOS and Linux are supported (got {plat})")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_installer.py -v 2>&1 | tail -12`
Expected: PASS — all 6 tests green.

- [ ] **Step 6: Commit**

```bash
git add reporter/templates/ reporter/installer.py tests/test_installer.py
git commit -m "feat: installer writes launchd/systemd unit files from templates"
```

---

## Task 9: `__main__.py` — argparse + `run`/`status` wiring (stub install/uninstall)

**Files:**
- Modify: `reporter/__main__.py` (replace the Task 1 stub)
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `config.load_config`, `collector.collect`, `mapper.map_points`, `uploader.upload`, `state.read_state`/`write_state`, `installer.install`/`uninstall`.
- Produces: `cli(argv: list[str] | None = None) -> int` — the entry point; returns the process exit code. Subcommands: `run`, `install`, `uninstall`, `status`. `run` orchestrates: load config → collect → map → upload (drains pending) → write state, translating `CollectorError`/`UploaderError`/`ConfigError` into their exit codes and updating `state.json` with `last_result`/`last_error`/`consecutive_failures`.

- [ ] **Step 1: Write the failing test `tests/test_cli.py`**

```python
import json
from pathlib import Path

import pytest

from reporter.__main__ import cli


def write_cfg(tmp_path, **over):
    p = tmp_path / "config.json"
    base = {"source_id": "mac", "insight_url": "http://127.0.0.1:8765"}
    base.update(over)
    p.write_text(json.dumps(base))
    return p


def test_run_success(tmp_path, monkeypatch):
    cfg = write_cfg(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    # stub collector + uploader
    monkeypatch.setattr("reporter.__main__.collect", lambda c: {"contributions": [
        {"date": "2026-07-02", "clients": [
            {"client": "zcode", "modelId": "glm-5.2",
             "tokens": {"input": 10, "output": 5}}]}]})
    monkeypatch.setattr("reporter.__main__.upload", lambda c, pts, sd, **k: (k, len(pts))[1])
    monkeypatch.setattr("time.time", lambda: 100.0)

    code = cli(["run", "--config", str(cfg)])
    assert code == 0
    state = json.loads((tmp_path / "state" / "ai-usage-reporter" / "state.json").read_text())
    assert state["last_result"] == "ok"
    assert state["consecutive_failures"] == 0
    assert state["points_sent_last"] == 1


def test_run_empty_contributions_is_success(tmp_path, monkeypatch):
    cfg = write_cfg(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr("reporter.__main__.collect", lambda c: {"contributions": []})
    monkeypatch.setattr("reporter.__main__.upload", lambda c, pts, sd, **k: len(pts))
    monkeypatch.setattr("time.time", lambda: 1.0)
    assert cli(["run", "--config", str(cfg)]) == 0


def test_run_collector_error_exits_and_records(tmp_path, monkeypatch):
    cfg = write_cfg(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    from reporter.collector import CollectorError
    monkeypatch.setattr("reporter.__main__.collect",
                       lambda c: (_ for _ in ()).throw(CollectorError("bin gone", 3)))
    monkeypatch.setattr("time.time", lambda: 1.0)
    code = cli(["run", "--config", str(cfg)])
    assert code == 3
    state = json.loads((tmp_path / "state" / "ai-usage-reporter" / "state.json").read_text())
    assert state["last_result"] == "error"
    assert state["consecutive_failures"] == 1
    assert "bin gone" in (state["last_error"] or "")


def test_run_uploader_error_exits_and_records(tmp_path, monkeypatch):
    cfg = write_cfg(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr("reporter.__main__.collect", lambda c: {"contributions": []})
    from reporter.uploader import UploaderError
    monkeypatch.setattr("reporter.__main__.upload",
                       lambda c, pts, sd, **k: (_ for _ in ()).throw(UploaderError("nope", 6)))
    monkeypatch.setattr("time.time", lambda: 1.0)
    assert cli(["run", "--config", str(cfg)]) == 6
    state = json.loads((tmp_path / "state" / "ai-usage-reporter" / "state.json").read_text())
    assert state["consecutive_failures"] == 1


def test_run_consecutive_failures_increments(tmp_path, monkeypatch):
    cfg = write_cfg(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    # seed a prior failure
    import reporter.state as st
    st.write_state(tmp_path / "state" / "ai-usage-reporter",
                   last_result="error", consecutive_failures=2)
    from reporter.collector import CollectorError
    monkeypatch.setattr("reporter.__main__.collect",
                       lambda c: (_ for _ in ()).throw(CollectorError("x", 4)))
    monkeypatch.setattr("time.time", lambda: 1.0)
    cli(["run", "--config", str(cfg)])
    state = json.loads((tmp_path / "state" / "ai-usage-reporter" / "state.json").read_text())
    assert state["consecutive_failures"] == 3


def test_run_resets_consecutive_failures_on_success(tmp_path, monkeypatch):
    cfg = write_cfg(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    import reporter.state as st
    st.write_state(tmp_path / "state" / "ai-usage-reporter", consecutive_failures=3)
    monkeypatch.setattr("reporter.__main__.collect", lambda c: {"contributions": []})
    monkeypatch.setattr("reporter.__main__.upload", lambda c, pts, sd, **k: len(pts))
    monkeypatch.setattr("time.time", lambda: 1.0)
    cli(["run", "--config", str(cfg)])
    state = json.loads((tmp_path / "state" / "ai-usage-reporter" / "state.json").read_text())
    assert state["consecutive_failures"] == 0


def test_status_prints_summary(tmp_path, monkeypatch, capsys):
    cfg = write_cfg(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    import reporter.state as st
    st.write_state(tmp_path / "state" / "ai-usage-reporter",
                   last_run_at="2026-07-02T14:38:00+08:00", last_result="ok",
                   points_sent_last=270)
    code = cli(["status", "--config", str(cfg)])
    assert code == 0
    out = capsys.readouterr().out
    assert "ok" in out
    assert "270" in out


def test_config_error_exits_2(tmp_path, monkeypatch):
    p = tmp_path / "missing.json"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert cli(["run", "--config", str(p)]) == 2


def test_install_invokes_installer(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("socket.gethostname", lambda: "mb")
    called = {}
    def fake_install(path, **k):
        called["path"] = path
        return {"config_path": str(path), "hints": ["do the thing"]}
    monkeypatch.setattr("reporter.__main__.install", fake_install)
    monkeypatch.setattr("sys.executable", "/usr/bin/python3")
    code = cli(["install"])
    assert code == 0
    assert "do the thing" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v 2>&1 | tail -5`
Expected: FAIL — `cli` still returns 2 stub, or `ImportError` for the subcommands.

- [ ] **Step 3: Write `reporter/__main__.py`**

```python
"""Entry point: argparse dispatch for run/install/uninstall/status."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from .collector import CollectorError, collect
from .config import ConfigError, default_config_path, default_state_dir, load_config
from .installer import install, uninstall
from .mapper import map_points
from .state import read_state, write_state
from .uploader import UploaderError, upload

log = logging.getLogger("reporter")

UTC8 = timezone(timedelta(hours=8))


def _state_dir_for(cfg_path: Path | None) -> Path:
    # state lives in the default XDG state dir regardless of config path,
    # unless XDG_STATE_HOME is set (handled by default_state_dir).
    return default_state_dir()


def _now_iso() -> str:
    return datetime.now(UTC8).isoformat(timespec="seconds")


def cmd_run(args) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return e.exit_code

    state_dir = _state_dir_for(args.config)
    state_dir.mkdir(parents=True, exist_ok=True)
    prior = read_state(state_dir)
    consecutive = prior.get("consecutive_failures", 0)

    try:
        raw = collect(cfg)
    except CollectorError as e:
        write_state(state_dir, last_run_at=_now_iso(), last_result="error",
                    last_error=str(e), consecutive_failures=consecutive + 1,
                    points_sent_last=0)
        print(f"collector error: {e}", file=sys.stderr)
        return e.exit_code

    points = map_points(raw)

    try:
        sent = upload(cfg, points, state_dir, now_ts=time.time())
    except UploaderError as e:
        write_state(state_dir, last_run_at=_now_iso(), last_result="error",
                    last_error=str(e), consecutive_failures=consecutive + 1,
                    points_sent_last=0)
        print(f"uploader error: {e}", file=sys.stderr)
        return e.exit_code

    write_state(state_dir, last_run_at=_now_iso(), last_result="ok",
                last_error=None, consecutive_failures=0, points_sent_last=sent)
    log.info("sent %d points", sent)
    return 0


def cmd_install(args) -> int:
    cfg_path = Path(args.config) if args.config else default_config_path()
    # resolved invocation: `python -m reporter run --config <path>` (or the
    # console-script binary if installed). We use the module form so a
    # non-pip-install is supported.
    reporter_cmd = [sys.executable, "-m", "reporter", "run", "--config", str(cfg_path)]
    try:
        result = install(cfg_path, reporter_cmd=reporter_cmd)
    except Exception as e:
        print(f"install error: {e}", file=sys.stderr)
        return 2
    print(f"config: {result['config_path']}")
    if "plist_path" in result:
        print(f"launchd plist: {result['plist_path']}")
    if "service_path" in result:
        print(f"systemd service: {result['service_path']}")
        print(f"systemd timer: {result['timer_path']}")
    for hint in result.get("hints", []):
        print(f"hint: {hint}")
    print("Edit the config to confirm source_id, then activate.")
    return 0


def cmd_uninstall(args) -> int:
    try:
        uninstall()
    except Exception as e:
        print(f"uninstall error: {e}", file=sys.stderr)
        return 2
    print("uninstalled timer units (config and state left intact)")
    return 0


def cmd_status(args) -> int:
    state_dir = _state_dir_for(args.config)
    s = read_state(state_dir)
    print(f"last_run_at:          {s.get('last_run_at')}")
    print(f"last_result:          {s.get('last_result')}")
    print(f"last_error:           {s.get('last_error')}")
    print(f"consecutive_failures: {s.get('consecutive_failures', 0)}")
    print(f"points_sent_last:     {s.get('points_sent_last', 0)}")
    print(f"pending_count:        {s.get('pending_count', 0)}")
    return 0


def cli(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="ai-usage-reporter")
    p.add_argument("--config", help="config path (default: XDG config path)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub_run = sub.add_parser("run", help="one collection cycle")
    sub_run.add_argument("--config", help="config path")
    sub_run.set_defaults(func=cmd_run)

    sub_install = sub.add_parser("install", help="write config template + timer unit")
    sub_install.add_argument("--config", help="config path")
    sub_install.set_defaults(func=cmd_install)

    sub_uninstall = sub.add_parser("uninstall", help="remove timer unit files")
    sub_uninstall.set_defaults(func=cmd_uninstall)

    sub_status = sub.add_parser("status", help="print run state")
    sub_status.add_argument("--config", help="config path")
    sub_status.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    # top-level --config before subcommand
    if getattr(args, "config", None) is None and not hasattr(args, "func"):
        # argparse already errored on missing subcommand; unreachable normally
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(cli())
```

Note: `--config` is accepted both before the subcommand (via the top-level option) and on each subcommand (via `sub_X.add_argument`). The subcommand-level copy takes precedence; `cmd_*` reads `args.config`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -v 2>&1 | tail -15`
Expected: PASS — all 9 tests green.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q 2>&1 | tail -10`
Expected: PASS — every test across all files green.

- [ ] **Step 6: Commit**

```bash
git add reporter/__main__.py tests/test_cli.py
git commit -m "feat: cli wires run/install/uninstall/status with state tracking"
```

---

## Task 10: README + manual smoke test + final wiring verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Produces: a README with install/run instructions and a verified end-to-end dry path (collect stubbed, upload stubbed) confirming the CLI exit codes behave per spec §8.

- [ ] **Step 1: Expand `README.md`**

```markdown
# ai-usage-reporter

A zero-dependency Python ≥3.10 CLI that reports per-model daily token usage
from `tokscale graph` into an [ai-plan-insight](https://github.com/) instance.
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
```

- [ ] **Step 2: Verify the console script resolves and `--help` works**

Run: `pip install -e . -q && ai-usage-reporter --help 2>&1 | head -20`
Expected: argparse usage listing `run`, `install`, `uninstall`, `status` subcommands; no traceback.

- [ ] **Step 3: Verify `status` with no state file prints defaults and exits 0**

Run: `XDG_STATE_HOME=/tmp/rpt-state-test ai-usage-reporter status 2>&1 | head -10; echo "exit=$?"`
Expected: six `key: ...` lines with `None`/`0` defaults, `exit=0`.

- [ ] **Step 4: Verify `run` against a missing config exits 2**

Run: `XDG_STATE_HOME=/tmp/rpt-state-test ai-usage-reporter run --config /tmp/nope.json 2>&1; echo "exit=$?"`
Expected: `config error: config file not found: /tmp/nope.json` (or similar), `exit=2`.

- [ ] **Step 5: Verify the full test suite still passes**

Run: `python -m pytest -q 2>&1 | tail -5`
Expected: PASS — all tests green.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: README with install/run/exit-code reference"
```

---

## Self-Review Notes

**Spec coverage check (spec section → task):**
- §3 tokscale JSON shape → Task 2 (fixture) + Task 4 (mapper reads `contributions[].clients[].modelId/tokens`).
- §4 mapping incl. merge pass → Task 4 (mapper merges `(date, model_id)`, drops cache/cost/etc.).
- §5 config + paths + validation → Task 3.
- §6 CLI surface (run/install/uninstall/status) → Task 9.
- §7 native timers (launchd plist, systemd service+timer, TZ=Asia/Shanghai, 15-min) → Task 8.
- §8 failure handling + replay + exit codes → Tasks 5 (3/4/5), 7 (6/7), 9 (state tracking, consecutive_failures), 6 (pending dir).
- §9 project layout → File Structure above; every listed file is created.
- §10 security (auth_token inert header) → Task 7 (`X-Report-Key` sent only when set).
- §11 testing strategy → each module has its test file matching spec's list.
- §12 out of scope → respected (Windows rejected, no cost forwarding, no daemon).

**Type/name consistency:** `map_points`, `collect`, `upload`, `post_payload`, `read_state`/`write_state`/`save_pending`/`list_pending`/`delete_pending`/`pending_dir`, `install`/`uninstall`, `cli`, `ConfigError`/`CollectorError`/`UploaderError`/`InstallerError` — names match across all interfaces and test files. Exit codes are the single source of truth (Global Constraints) and referenced consistently.

**Placeholder scan:** No TBD/TODO/"implement later". Every code step contains real code. No "similar to Task N" — each task's code is self-contained.
