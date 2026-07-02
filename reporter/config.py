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
