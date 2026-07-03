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
            f"tokscale launcher not executable: {name_or_path} "
            f"(default launcher is `npx`, shipped with Node.js — see https://nodejs.org/)",
            3,
        )
    resolved = shutil.which(name_or_path)
    if resolved:
        return resolved
    raise CollectorError(
        f"tokscale launcher '{name_or_path}' not found on PATH "
        f"(default launcher is `npx`, shipped with Node.js — see https://nodejs.org/)",
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
