import json
from pathlib import Path
from unittest.mock import patch

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
    plist = home / "Library" / "LaunchAgents" / "ai-usage-reporter.plist"
    monkeypatch.setattr("reporter.installer._launchd_plist_path", lambda: plist)

    cfg_path = tmp_path / "xdg" / "ai-usage-reporter" / "config.json"
    result = install(cfg_path, reporter_cmd=["/usr/local/bin/ai-usage-reporter", "run", "--config", str(cfg_path)])

    assert plist.exists()
    text = plist.read_text()
    assert "ai.usage-reporter.agent" in text
    assert "<integer>300</integer>" in text
    assert "Asia/Shanghai" not in text  # no TZ override: tokscale buckets by local day
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
    units = tmp_path / "home" / ".config" / "systemd" / "user"
    monkeypatch.setattr("reporter.installer._systemd_unit_dir", lambda: units)

    cfg_path = tmp_path / "xdg" / "ai-usage-reporter" / "config.json"
    install(cfg_path, reporter_cmd=["/usr/bin/ai-usage-reporter", "run", "--config", str(cfg_path)])

    svc = (units / "ai-usage-reporter.service").read_text()
    tmr = (units / "ai-usage-reporter.timer").read_text()
    assert "Type=oneshot" in svc
    assert "ExecStart=/usr/bin/ai-usage-reporter run --config" in svc
    assert "TZ=" not in svc  # no TZ override: tokscale buckets by local day
    assert "OnCalendar=*:0/5" in tmr
    assert "Persistent=true" in tmr


def test_install_unsupported_platform_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "FreeBSD")
    with pytest.raises(InstallerError):
        install(tmp_path / "c.json", reporter_cmd=["x"])


def test_install_windows_writes_scheduled_task(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("socket.gethostname", lambda: "winbox")

    cfg_path = tmp_path / "xdg" / "ai-usage-reporter" / "config.json"
    with patch("reporter.installer.subprocess") as mock_sub:
        mock_sub.run.return_value = None
        result = install(cfg_path, reporter_cmd=[
            "C:\\Python312\\python.exe", "-m", "reporter", "run",
            "--config", str(cfg_path),
        ])

    bat_path = tmp_path / "state" / "ai-usage-reporter" / "run-reporter.bat"
    vbs_path = tmp_path / "state" / "ai-usage-reporter" / "run-reporter.vbs"
    xml_path = tmp_path / "state" / "ai-usage-reporter" / "ai-usage-reporter-task.xml"
    assert bat_path.exists()
    assert vbs_path.exists()
    assert xml_path.exists()

    bat_text = bat_path.read_text(encoding="utf-8")
    assert "TZ=" not in bat_text  # no TZ override: tokscale buckets by local day
    assert "C:\\Python312\\python.exe" in bat_text
    assert "run.log" in bat_text
    assert ">>" in bat_text and "2>&1" in bat_text

    vbs_text = vbs_path.read_text(encoding="utf-8")
    assert "WScript.Shell" in vbs_text
    assert "run-reporter.bat" in vbs_text

    xml_text = xml_path.read_text(encoding="utf-16")
    assert "PT5M" in xml_text
    assert "<ExecutionTimeLimit>PT4M</ExecutionTimeLimit>" in xml_text
    assert "wscript.exe" in xml_text
    assert "run-reporter.vbs" in xml_text

    data = json.loads(cfg_path.read_text())
    assert data["source_id"] == "<winbox>"
    assert "task_xml_path" in result
    assert "task_bat_path" in result
    assert "task_vbs_path" in result


def test_install_does_not_overwrite_existing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("socket.gethostname", lambda: "mb")
    monkeypatch.setattr(
        "reporter.installer._launchd_plist_path",
        lambda: tmp_path / "home" / "Library" / "LaunchAgents" / "ai-usage-reporter.plist",
    )

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
    monkeypatch.setattr("reporter.installer._launchd_plist_path", lambda: plist)
    uninstall()
    assert not plist.exists()


def test_uninstall_linux_removes_units(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr("platform.system", lambda: "Linux")
    units = tmp_path / "home" / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    (units / "ai-usage-reporter.service").write_text("x")
    (units / "ai-usage-reporter.timer").write_text("x")
    monkeypatch.setattr("reporter.installer._systemd_unit_dir", lambda: units)
    uninstall()
    assert not (units / "ai-usage-reporter.service").exists()
    assert not (units / "ai-usage-reporter.timer").exists()


def test_uninstall_windows_removes_task(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr("platform.system", lambda: "Windows")
    state = tmp_path / "state" / "ai-usage-reporter"
    state.mkdir(parents=True)
    xml_path = state / "ai-usage-reporter-task.xml"
    bat_path = state / "run-reporter.bat"
    vbs_path = state / "run-reporter.vbs"
    xml_path.write_text("dummy")
    bat_path.write_text("dummy")
    vbs_path.write_text("dummy")
    with patch("reporter.installer.subprocess") as mock_sub:
        mock_sub.run.return_value = None
        uninstall()
    assert not xml_path.exists()
    assert not bat_path.exists()
    assert not vbs_path.exists()
