import json

from reporter.state import (
    delete_pending, load_pending, migrate_legacy_pending, pending_path,
    read_state, save_pending, write_state,
)


def test_read_state_missing_returns_defaults(tmp_path):
    s = read_state(tmp_path)
    assert s == {
        "last_run_at": None, "last_result": None, "last_error": None,
        "consecutive_failures": 0, "points_sent_last": 0, "pending_count": 0,
    }


def test_write_state_merges_and_counts_pending(tmp_path):
    save_pending(tmp_path, {"points": []})
    s = write_state(tmp_path, last_run_at="2026-07-02T14:00:00+08:00", last_result="ok")
    assert s["last_result"] == "ok"
    assert s["pending_count"] == 1


def test_save_pending_is_a_single_file_newest_wins(tmp_path):
    save_pending(tmp_path, {"reported_at": "2026-07-01"})
    p = save_pending(tmp_path, {"reported_at": "2026-07-02"})
    assert p == pending_path(tmp_path)
    assert load_pending(tmp_path)["reported_at"] == "2026-07-02"
    # no leftover temp files
    assert not list(tmp_path.glob("*.tmp"))


def test_load_pending_missing_returns_none(tmp_path):
    assert load_pending(tmp_path) is None


def test_load_pending_discards_corrupt_file(tmp_path):
    pending_path(tmp_path).write_text("{not json")
    assert load_pending(tmp_path) is None
    assert not pending_path(tmp_path).exists()


def test_delete_pending_idempotent(tmp_path):
    save_pending(tmp_path, {})
    delete_pending(tmp_path)
    delete_pending(tmp_path)  # no error
    assert not pending_path(tmp_path).exists()


def test_migrate_legacy_pending_dir_keeps_newest(tmp_path):
    legacy = tmp_path / "pending"
    legacy.mkdir()
    (legacy / "0000000010.json").write_text(json.dumps({"reported_at": "old"}))
    (legacy / "0000000099.json").write_text(json.dumps({"reported_at": "new"}))
    migrate_legacy_pending(tmp_path)
    assert not legacy.exists()
    assert load_pending(tmp_path)["reported_at"] == "new"


def test_migrate_legacy_pending_keeps_existing_pending_json(tmp_path):
    save_pending(tmp_path, {"reported_at": "current"})
    legacy = tmp_path / "pending"
    legacy.mkdir()
    (legacy / "0000000010.json").write_text(json.dumps({"reported_at": "old"}))
    migrate_legacy_pending(tmp_path)
    assert load_pending(tmp_path)["reported_at"] == "current"
    assert not legacy.exists()


def test_write_state_atomic(tmp_path):
    write_state(tmp_path, last_result="ok")
    assert (tmp_path / "state.json").exists()
    # no leftover temp files
    assert not list((tmp_path).glob("*.tmp"))
