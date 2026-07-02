"""state.json + pending directory management."""
from __future__ import annotations

import json
import os
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


def pending_dir(state_dir: Path) -> Path:
    d = Path(state_dir) / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


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
    merged["pending_count"] = len(list_pending(state_dir))
    return merged


def write_state(state_dir: Path, **fields) -> dict:
    state = read_state(state_dir)
    state.update(fields)
    state["pending_count"] = len(list_pending(state_dir))
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


def save_pending(state_dir: Path, payload: dict, *, ts: float) -> Path:
    """Write payload to pending/<ts>.json. ts is supplied by the caller."""
    d = pending_dir(state_dir)
    # filename uses the integer ts to keep lexical sort == chronological
    name = f"{int(ts):010d}.json"
    p = d / name
    p.write_text(json.dumps(payload))
    return p


def list_pending(state_dir: Path) -> list[Path]:
    d = Path(state_dir) / "pending"
    if not d.exists():
        return []
    return sorted(d.glob("*.json"))


def delete_pending(path: Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
