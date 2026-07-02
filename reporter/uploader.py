"""POST snapshots to insight + replay the pending one. Exit codes 6/7."""
from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .config import Config
from .state import delete_pending, load_pending, save_pending

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


def build_payload(config: Config, points: list[dict], reported_at: str) -> dict:
    """reported_at is this run's UTC+8 date; insight freezes all days before
    it once the payload lands, so it must travel with the payload (a replayed
    pending file keeps its own, older date)."""
    return {
        "source_id": config.source_id,
        "source_label": config.source_label,
        "reported_at": reported_at,
        "points": points,
    }


def post_payload(config: Config, payload: dict, *, poster: Callable) -> None:
    """POST one payload. Raises UploaderError(6/7)."""
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if config.auth_token:
        headers["X-Report-Key"] = config.auth_token
    try:
        status, _ = poster(config.report_url(), body, headers, config.request_timeout_seconds)
    except (urllib.error.URLError, socket.timeout) as e:
        raise UploaderError(f"insight unreachable: {e}", 6) from None
    if not (200 <= status < 300):
        raise UploaderError(f"insight returned HTTP {status}", 7)


def upload(config: Config, points: list[dict], reported_at: str, state_dir: Path,
           *, poster: Callable | None = None) -> int:
    """Replay the pending snapshot (if any), then POST the fresh one.

    The pending payload may still hold days the local data has since lost, so
    it goes first; the fresh payload then overwrites everything it covers. On
    any failure the fresh payload becomes the new pending (a full-window
    snapshot supersedes any older one) and the UploaderError is re-raised.
    Returns the number of points in the fresh payload on success.
    """
    post = poster or _default_poster
    fresh = build_payload(config, points, reported_at)
    pending = load_pending(state_dir)
    try:
        if pending is not None:
            post_payload(config, pending, poster=post)
            log.info("replayed pending snapshot")
        post_payload(config, fresh, poster=post)
    except UploaderError as e:
        save_pending(state_dir, fresh)
        raise e
    delete_pending(state_dir)
    return len(points)
