"""Entry point: argparse dispatch for run/install/uninstall/status."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from .collector import CollectorError, collect
from .config import ConfigError, default_config_path, default_state_dir, load_config
from .installer import install, uninstall
from .mapper import map_points
from .state import read_state, write_state
from .uploader import UploaderError, upload

log = logging.getLogger("reporter")


def _local_today(now: datetime | None = None) -> str:
    """Today's date in the machine's local timezone.

    This is the day boundary tokscale buckets usage into (it ignores TZ), so
    it is also the only correct value for `reported_at`: insight freezes every
    day before it, and a watermark running ahead of the local day would freeze
    the current day while tokscale is still adding to it.
    """
    return (now or datetime.now().astimezone()).strftime("%Y-%m-%d")


def _state_dir_for(cfg_path: Path | None) -> Path:
    # state lives in the default XDG state dir regardless of config path,
    # unless XDG_STATE_HOME is set (handled by default_state_dir).
    return default_state_dir()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def cmd_run(args) -> int:
    config_path = args.config or default_config_path()
    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return e.exit_code

    state_dir = _state_dir_for(config_path)
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

    reported_at = _local_today()
    try:
        sent = upload(cfg, points, reported_at, state_dir)
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
    if "task_xml_path" in result:
        print(f"task scheduler XML: {result['task_xml_path']}")
        print(f"wrapper bat: {result['task_bat_path']}")
        print(f"wrapper vbs: {result['task_vbs_path']}")
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
    state_dir = _state_dir_for(args.config or default_config_path())
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
