import json
import socket
import urllib.error

import pytest

from reporter.config import Config
from reporter.state import load_pending, pending_path, save_pending
from reporter.uploader import UploaderError, build_payload, post_payload, upload


def cfg(**over) -> Config:
    base = dict(source_id="m", insight_url="http://localhost:8765",
                request_timeout_seconds=30)
    base.update(over)
    return Config(**base)


def fake_poster(status=200):
    """Returns (runner, received) where received is a list of (url, body)."""
    received = []

    def run(url, body, headers, timeout):
        received.append((url, json.loads(body), headers, timeout))
        return status, b""

    return run, received


def fresh(points=None, reported_at="2026-07-02"):
    return build_payload(cfg(), points or [], reported_at)


def test_post_payload_shape():
    run, received = fake_poster()
    c = cfg()
    payload = build_payload(c, [{"date": "2026-07-02", "model_id": "glm-5.2",
                                 "input_tokens": 10, "output_tokens": 5}], "2026-07-02")
    post_payload(c, payload, poster=run)
    url, body, headers, timeout = received[0]
    # report_url = insight_url.rstrip('/') + '/api/usage/report'
    assert url == "http://localhost:8765/api/usage/report"
    assert body["source_id"] == "m"
    assert body["source_label"] is None
    assert body["reported_at"] == "2026-07-02"
    assert body["points"] == [{"date": "2026-07-02", "model_id": "glm-5.2",
                                "input_tokens": 10, "output_tokens": 5}]
    assert timeout == 30


def test_post_payload_sends_auth_header_when_set():
    run, received = fake_poster()
    c = cfg(auth_token="sekret")
    post_payload(c, fresh(), poster=run)
    assert received[0][2].get("X-Report-Key") == "sekret"


def test_post_payload_no_auth_header_when_unset():
    run, received = fake_poster()
    post_payload(cfg(), fresh(), poster=run)
    assert "X-Report-Key" not in received[0][2]


def test_non_2xx_exits_7():
    run, _ = fake_poster(status=500)
    with pytest.raises(UploaderError) as exc:
        post_payload(cfg(), fresh(), poster=run)
    assert exc.value.exit_code == 7


def test_url_error_exits_6():
    def run(url, body, headers, timeout):
        raise urllib.error.URLError("connection refused")
    with pytest.raises(UploaderError) as exc:
        post_payload(cfg(), fresh(), poster=run)
    assert exc.value.exit_code == 6


def test_socket_timeout_exits_6():
    def run(url, body, headers, timeout):
        raise socket.timeout("timed out")
    with pytest.raises(UploaderError) as exc:
        post_payload(cfg(), fresh(), poster=run)
    assert exc.value.exit_code == 6


def test_upload_replays_pending_first_with_its_own_reported_at(tmp_path):
    # pre-existing pending payload from a prior failed run (older reported_at)
    save_pending(tmp_path, build_payload(
        cfg(), [{"date": "2026-07-01", "model_id": "glm-5.2",
                 "input_tokens": 1, "output_tokens": 1}], "2026-07-01"))
    run, received = fake_poster()
    upload(cfg(), [], "2026-07-02", tmp_path, poster=run)
    # pending posted first (keeping its original reported_at), then fresh
    assert len(received) == 2
    assert received[0][1]["reported_at"] == "2026-07-01"
    assert received[0][1]["points"][0]["date"] == "2026-07-01"
    assert received[1][1]["reported_at"] == "2026-07-02"
    assert received[1][1]["points"] == []
    # pending deleted after success
    assert load_pending(tmp_path) is None


def test_upload_failure_keeps_only_the_fresh_snapshot(tmp_path):
    save_pending(tmp_path, fresh(reported_at="2026-07-01"))
    run, _ = fake_poster(status=500)
    with pytest.raises(UploaderError):
        upload(cfg(), [{"date": "2026-07-02", "model_id": "glm-5.2",
                        "input_tokens": 9, "output_tokens": 9}],
               "2026-07-02", tmp_path, poster=run)
    # the fresh full-window snapshot supersedes the old pending
    pend = load_pending(tmp_path)
    assert pend["reported_at"] == "2026-07-02"
    assert pend["points"][0]["input_tokens"] == 9


def test_upload_saves_fresh_on_failure(tmp_path):
    run, _ = fake_poster(status=503)
    with pytest.raises(UploaderError):
        upload(cfg(), [{"date": "2026-07-02", "model_id": "glm-5.2",
                        "input_tokens": 9, "output_tokens": 9}],
               "2026-07-02", tmp_path, poster=run)
    pend = load_pending(tmp_path)
    assert pend["points"][0]["input_tokens"] == 9


def test_upload_fresh_saved_when_only_replay_succeeds(tmp_path):
    save_pending(tmp_path, fresh(reported_at="2026-07-01"))
    statuses = iter([200, 500])

    def run(url, body, headers, timeout):
        return next(statuses), b""

    with pytest.raises(UploaderError):
        upload(cfg(), [{"date": "2026-07-02", "model_id": "glm-5.2",
                        "input_tokens": 3, "output_tokens": 3}],
               "2026-07-02", tmp_path, poster=run)
    assert load_pending(tmp_path)["reported_at"] == "2026-07-02"


def test_upload_returns_points_sent(tmp_path):
    run, _ = fake_poster()
    pts = [{"date": "2026-07-02", "model_id": "glm-5.2", "input_tokens": 1, "output_tokens": 1}]
    n = upload(cfg(), pts, "2026-07-02", tmp_path, poster=run)
    assert n == 1


def test_upload_no_pending_single_post(tmp_path):
    run, received = fake_poster()
    upload(cfg(), [], "2026-07-02", tmp_path, poster=run)
    assert len(received) == 1  # only the fresh post


def test_upload_replays_legacy_pending_dir(tmp_path):
    # old layout: pending/<ts>.json files; the newest one wins
    legacy = tmp_path / "pending"
    legacy.mkdir()
    (legacy / "0000000010.json").write_text(json.dumps(fresh(reported_at="2026-06-30")))
    (legacy / "0000000099.json").write_text(json.dumps(fresh(reported_at="2026-07-01")))
    run, received = fake_poster()
    upload(cfg(), [], "2026-07-02", tmp_path, poster=run)
    assert len(received) == 2
    assert received[0][1]["reported_at"] == "2026-07-01"
    assert not legacy.exists()
    assert not pending_path(tmp_path).exists()
