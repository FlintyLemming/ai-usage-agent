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
