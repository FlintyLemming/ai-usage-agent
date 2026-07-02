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
