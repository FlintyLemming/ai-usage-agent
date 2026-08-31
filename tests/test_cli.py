import json
from datetime import datetime, timedelta, timezone
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
    monkeypatch.setattr("reporter.__main__.upload", lambda c, pts, ra, sd, **k: len(pts))

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
    monkeypatch.setattr("reporter.__main__.upload", lambda c, pts, ra, sd, **k: len(pts))
    assert cli(["run", "--config", str(cfg)]) == 0


def test_run_collector_error_exits_and_records(tmp_path, monkeypatch):
    cfg = write_cfg(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    from reporter.collector import CollectorError
    monkeypatch.setattr("reporter.__main__.collect",
                       lambda c: (_ for _ in ()).throw(CollectorError("bin gone", 3)))
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
                       lambda c, pts, ra, sd, **k: (_ for _ in ()).throw(UploaderError("nope", 6)))
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
    cli(["run", "--config", str(cfg)])
    state = json.loads((tmp_path / "state" / "ai-usage-reporter" / "state.json").read_text())
    assert state["consecutive_failures"] == 3


def test_run_resets_consecutive_failures_on_success(tmp_path, monkeypatch):
    cfg = write_cfg(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    import reporter.state as st
    st.write_state(tmp_path / "state" / "ai-usage-reporter", consecutive_failures=3)
    monkeypatch.setattr("reporter.__main__.collect", lambda c: {"contributions": []})
    monkeypatch.setattr("reporter.__main__.upload", lambda c, pts, ra, sd, **k: len(pts))
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


def test_run_falls_back_to_default_config_path(tmp_path, monkeypatch):
    """run without --config should use default_config_path(), not crash on None."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    # default config does not exist -> ConfigError -> exit 2
    assert cli(["run"]) == 2


def test_status_falls_back_to_default_config_path(tmp_path, monkeypatch, capsys):
    """status without --config should not crash; prints defaults, exit 0."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert cli(["status"]) == 0
    assert "last_result" in capsys.readouterr().out


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


def test_local_today_uses_machine_local_day_not_utc8():
    from reporter.__main__ import _local_today

    # UTC-7 at 20:12 -> already 2026-08-31 in UTC+8, but the local day, which is
    # the day tokscale buckets into, is still 2026-08-30.
    pdt = timezone(timedelta(hours=-7))
    assert _local_today(datetime(2026, 8, 30, 20, 12, tzinfo=pdt)) == "2026-08-30"


def test_run_sends_local_day_as_reported_at(tmp_path, monkeypatch):
    cfg = write_cfg(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr("reporter.__main__.collect", lambda c: {"contributions": []})
    monkeypatch.setattr("reporter.__main__._local_today", lambda now=None: "2026-08-30")

    seen = {}

    def fake_upload(c, pts, ra, sd, **k):
        seen["reported_at"] = ra
        return len(pts)

    monkeypatch.setattr("reporter.__main__.upload", fake_upload)

    assert cli(["run", "--config", str(cfg)]) == 0
    # The freeze watermark must never run ahead of the day the points are
    # bucketed in, or the current day is frozen while it is still growing.
    assert seen["reported_at"] == "2026-08-30"
