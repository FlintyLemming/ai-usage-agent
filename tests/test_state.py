import json
from pathlib import Path

from reporter.state import (
    delete_pending, list_pending, pending_dir, read_state, save_pending, write_state,
)


def test_read_state_missing_returns_defaults(tmp_path):
    s = read_state(tmp_path)
    assert s == {
        "last_run_at": None, "last_result": None, "last_error": None,
        "consecutive_failures": 0, "points_sent_last": 0, "pending_count": 0,
    }


def test_write_state_merges_and_counts_pending(tmp_path):
    save_pending(tmp_path, {"points": []}, ts=100.0)
    save_pending(tmp_path, {"points": []}, ts=50.0)
    s = write_state(tmp_path, last_run_at="2026-07-02T14:00:00+08:00", last_result="ok")
    assert s["last_result"] == "ok"
    assert s["pending_count"] == 2


def test_save_pending_uses_ts_in_filename(tmp_path):
    p = save_pending(tmp_path, {"points": [{"date": "x"}]}, ts=1234567.89)
    assert p.parent == tmp_path / "pending"
    # filename is zero-padded so lexical sort == chronological (see test_list_pending)
    assert p.name == "0001234567.json"
    assert json.loads(p.read_text())["points"][0]["date"] == "x"


def test_list_pending_sorted_oldest_first(tmp_path):
    save_pending(tmp_path, {}, ts=200.0)
    save_pending(tmp_path, {}, ts=10.0)
    save_pending(tmp_path, {}, ts=100.0)
    names = [p.name for p in list_pending(tmp_path)]
    assert names == sorted(names)  # ascending


def test_delete_pending_idempotent(tmp_path):
    p = save_pending(tmp_path, {}, ts=1.0)
    delete_pending(p)
    delete_pending(p)  # no error
    assert not p.exists()


def test_write_state_atomic(tmp_path):
    write_state(tmp_path, last_result="ok")
    assert (tmp_path / "state.json").exists()
    # no leftover temp files
    assert not list((tmp_path).glob("*.tmp"))
