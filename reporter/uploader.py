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

    On any failure (pending replay or fresh post) the already-replayed pending
    files are gone and the surviving ones stay in place; the fresh payload is
    also saved to pending so the next successful run retries it. Re-raises the
    UploaderError. Returns the number of points in the fresh payload on success.
    """
    post = poster or _default_poster
    err: UploaderError | None = None

    # 1. drain pending (oldest first); leave failed files in place
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
            err = e
            break
        delete_pending(p)
        log.info("replayed pending %s", p)

    # 2. fresh post
    if err is None:
        try:
            post_payload(config, points, poster=post)
        except UploaderError as e:
            err = e

    if err is not None:
        # persist this cycle's payload so the next successful run retries it
        if now_ts is not None:
            save_pending(state_dir, {
                "source_id": config.source_id,
                "source_label": config.source_label,
                "points": points,
            }, ts=now_ts)
        raise err
    return len(points)
