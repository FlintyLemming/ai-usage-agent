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
        tokscale_bin="npx", tokscale_args=["tokscale@latest", "graph"], lookback_days=90,
    )
    base.update(over)
    return Config(**base)


def fake_runner(stdout: bytes = b'{"contributions":[]}', returncode: int = 0, stderr: bytes = b""):
    calls = []

    def run(argv, env):
        calls.append((argv, env))
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    return run, calls


def _put_npx_on_path(tmp_path, monkeypatch):
    """Stub shutil.which so binary resolution succeeds without a real npx.

    Tests that inject a `runner` are exercising the runner/argv/env/exit-code
    paths; binary resolution is a precondition, not the unit under test.
    """
    fake = tmp_path / "npx"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda name: str(fake) if name == "npx" else None)
    return fake


def test_collect_invokes_tokscale_with_since(tmp_path, monkeypatch, tokscale_sample):
    _put_npx_on_path(tmp_path, monkeypatch)
    run, calls = fake_runner(json.dumps(tokscale_sample).encode())
    cfg = make_cfg()
    data = collect(cfg, runner=run)
    argv = calls[0][0]
    assert argv[0].endswith("npx") or argv[0] == "npx"
    assert "tokscale@latest" in argv
    assert "--since" in argv
    assert "graph" in argv


def test_collect_does_not_override_tz(tmp_path, monkeypatch):
    # tokscale ignores TZ and buckets by the machine's local day, so forcing a
    # TZ here would only desync our dates from the buckets it actually emits.
    _put_npx_on_path(tmp_path, monkeypatch)
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    run, calls = fake_runner(b'{"contributions":[]}')
    collect(make_cfg(), runner=run)
    assert calls[0][1].get("TZ") == "America/Los_Angeles"


def test_collect_leaves_tz_unset_when_ambient_has_none(tmp_path, monkeypatch):
    _put_npx_on_path(tmp_path, monkeypatch)
    monkeypatch.delenv("TZ", raising=False)
    run, calls = fake_runner(b'{"contributions":[]}')
    collect(make_cfg(), runner=run)
    assert "TZ" not in calls[0][1]


def test_collect_makes_npx_noninteractive(tmp_path, monkeypatch):
    _put_npx_on_path(tmp_path, monkeypatch)
    run, calls = fake_runner(b'{"contributions":[]}')
    collect(make_cfg(), runner=run)
    env = calls[0][1]
    assert env.get("npm_config_yes") == "true"


def test_collect_parses_json(tmp_path, monkeypatch, tokscale_sample):
    _put_npx_on_path(tmp_path, monkeypatch)
    run, _ = fake_runner(json.dumps(tokscale_sample).encode())
    data = collect(make_cfg(), runner=run)
    assert "contributions" in data
    assert len(data["contributions"]) == 2


def test_binary_missing_exits_3(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(CollectorError) as exc:
        collect(make_cfg(tokscale_bin="nope-npx"))
    assert exc.value.exit_code == 3


def test_binary_not_executable_exits_3(tmp_path, monkeypatch):
    fake = tmp_path / "npx"
    fake.write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setattr("os.access", lambda path, mode: False)
    monkeypatch.setattr("shutil.which", lambda name: str(fake) if name == "npx" else None)
    with pytest.raises(CollectorError) as exc:
        collect(make_cfg(tokscale_bin=str(fake)))
    assert exc.value.exit_code == 3


def test_tokscale_nonzero_exits_4(tmp_path, monkeypatch):
    _put_npx_on_path(tmp_path, monkeypatch)
    run, _ = fake_runner(b"oops", returncode=1, stderr=b"error: boom")
    with pytest.raises(CollectorError) as exc:
        collect(make_cfg(), runner=run)
    assert exc.value.exit_code == 4
    assert "boom" in str(exc.value)


def test_tokscale_timeout_exits_4(tmp_path, monkeypatch):
    _put_npx_on_path(tmp_path, monkeypatch)

    def run(argv, env):
        raise subprocess.TimeoutExpired(argv, 240)

    with pytest.raises(CollectorError) as exc:
        collect(make_cfg(), runner=run)
    assert exc.value.exit_code == 4
    assert "timed out after 240 seconds" in str(exc.value)


def test_bad_json_exits_5(tmp_path, monkeypatch):
    _put_npx_on_path(tmp_path, monkeypatch)
    run, _ = fake_runner(b"not json at all")
    with pytest.raises(CollectorError) as exc:
        collect(make_cfg(), runner=run)
    assert exc.value.exit_code == 5


def test_since_date_default():
    # 2026-07-02 minus 90 days = 2026-04-03
    now = datetime(2026, 7, 2, 15, 0, tzinfo=UTC8)
    assert since_date(90, now=now) == "2026-04-03"


def test_since_date_zero_lookback():
    now = datetime(2026, 7, 2, 0, 30, tzinfo=UTC8)
    assert since_date(0, now=now) == "2026-07-02"


def test_since_date_uses_machine_local_day_not_utc8():
    # A machine at UTC-7 at 20:12 local: it is already 2026-08-31 in UTC+8,
    # but tokscale's newest bucket is the local day 2026-08-30. The window we
    # ask for must be anchored to that local day.
    pdt = timezone(timedelta(hours=-7))
    now = datetime(2026, 8, 30, 20, 12, tzinfo=pdt)
    assert since_date(0, now=now) == "2026-08-30"
    assert since_date(90, now=now) == "2026-06-01"
