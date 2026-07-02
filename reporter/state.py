"""state.json + pending payload management.

Pending is a single file (pending.json): every payload is a full-window
snapshot, so only the newest failed one is worth replaying — older ones are
strict subsets of it.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

DEFAULT_STATE = {
    "last_run_at": None,
    "last_result": None,
    "last_error": None,
    "consecutive_failures": 0,
    "points_sent_last": 0,
    "pending_count": 0,
}


def pending_path(state_dir: Path) -> Path:
    return Path(state_dir) / "pending.json"


def read_state(state_dir: Path) -> dict:
    f = Path(state_dir) / "state.json"
    if not f.exists():
        return dict(DEFAULT_STATE)
    try:
        data = json.loads(f.read_text())
    except json.JSONDecodeError:
        return dict(DEFAULT_STATE)
    merged = dict(DEFAULT_STATE)
    merged.update(data)
    merged["pending_count"] = 1 if has_pending(state_dir) else 0
    return merged


def write_state(state_dir: Path, **fields) -> dict:
    state = read_state(state_dir)
    state.update(fields)
    state["pending_count"] = 1 if has_pending(state_dir) else 0
    out = Path(state_dir) / "state.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # atomic write: temp in same dir, then rename
    fd, tmppath = tempfile.mkstemp(dir=out.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmppath, out)
    except Exception:
        try:
            os.unlink(tmppath)
        except OSError:
            pass
        raise
    return state


def save_pending(state_dir: Path, payload: dict) -> Path:
    """Overwrite pending.json with the newest failed payload."""
    p = pending_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmppath = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.replace(tmppath, p)
    except Exception:
        try:
            os.unlink(tmppath)
        except OSError:
            pass
        raise
    return p


def load_pending(state_dir: Path) -> dict | None:
    """Return the pending payload, or None. A corrupt file is discarded."""
    migrate_legacy_pending(state_dir)
    p = pending_path(state_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        delete_pending(state_dir)
        return None


def delete_pending(state_dir: Path) -> None:
    try:
        pending_path(state_dir).unlink()
    except FileNotFoundError:
        pass


def has_pending(state_dir: Path) -> bool:
    return pending_path(state_dir).exists() or bool(_legacy_pending_files(state_dir))


def _legacy_pending_files(state_dir: Path) -> list[Path]:
    d = Path(state_dir) / "pending"
    if not d.is_dir():
        return []
    return sorted(d.glob("*.json"))


def migrate_legacy_pending(state_dir: Path) -> None:
    """Collapse the old pending/ directory into pending.json (newest wins)."""
    legacy = _legacy_pending_files(state_dir)
    if legacy and not pending_path(state_dir).exists():
        shutil.copyfile(legacy[-1], pending_path(state_dir))
    d = Path(state_dir) / "pending"
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
