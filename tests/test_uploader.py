import json
import socket
import urllib.error
from urllib.request import urlopen

import pytest

from reporter.config import Config
from reporter.state import list_pending, save_pending
from reporter.uploader import UploaderError, post_payload, upload


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


def test_post_payload_shape():
    run, received = fake_poster()
    c = cfg()
    post_payload(c, [{"date": "2026-07-02", "model_id": "glm-5.2",
                      "input_tokens": 10, "output_tokens": 5}], poster=run)
    url, body, headers, timeout = received[0]
    # report_url = insight_url.rstrip('/') + '/api/usage/report'
    assert url == "http://localhost:8765/api/usage/report"
    assert body["source_id"] == "m"
    assert body["source_label"] is None
    assert body["points"] == [{"date": "2026-07-02", "model_id": "glm-5.2",
                                "input_tokens": 10, "output_tokens": 5}]
    assert timeout == 30


def test_post_payload_sends_auth_header_when_set():
    run, received = fake_poster()
    c = cfg(auth_token="sekret")
    post_payload(c, [], poster=run)
    assert received[0][2].get("X-Report-Key") == "sekret"


def test_post_payload_no_auth_header_when_unset():
    run, received = fake_poster()
    post_payload(cfg(), [], poster=run)
    assert "X-Report-Key" not in received[0][2]


def test_non_2xx_exits_7():
    run, _ = fake_poster(status=500)
    with pytest.raises(UploaderError) as exc:
        post_payload(cfg(), [], poster=run)
    assert exc.value.exit_code == 7


def test_url_error_exits_6():
    def run(url, body, headers, timeout):
        raise urllib.error.URLError("connection refused")
    with pytest.raises(UploaderError) as exc:
        post_payload(cfg(), [], poster=run)
    assert exc.value.exit_code == 6


def test_socket_timeout_exits_6():
    def run(url, body, headers, timeout):
        raise socket.timeout("timed out")
    with pytest.raises(UploaderError) as exc:
        post_payload(cfg(), [], poster=run)
    assert exc.value.exit_code == 6


def test_upload_drains_pending_first(tmp_path):
    # pre-existing pending file from a prior failed run
    save_pending(tmp_path, {"source_id": "m", "source_label": None,
                            "points": [{"date": "2026-07-01", "model_id": "glm-5.2",
                                        "input_tokens": 1, "output_tokens": 1}]}, ts=10.0)
    run, received = fake_poster()
    upload(cfg(), [], tmp_path, poster=run, now_ts=100.0)
    # pending posted first, then fresh (empty points) posted second
    assert len(received) == 2
    assert received[0][1]["points"][0]["date"] == "2026-07-01"
    assert received[1][1]["points"] == []
    # pending file deleted after success
    assert list_pending(tmp_path) == []


def test_upload_leaves_pending_on_failure(tmp_path):
    save_pending(tmp_path, {"source_id": "m", "source_label": None, "points": []}, ts=10.0)
    run, _ = fake_poster(status=500)
    with pytest.raises(UploaderError):
        upload(cfg(), [], tmp_path, poster=run, now_ts=100.0)
    # the old pending file stays; the fresh payload also gets saved
    pend = list_pending(tmp_path)
    assert len(pend) == 2
    # oldest is the original (ts=10), newest is the fresh (ts=100)
    names = sorted(p.name for p in pend)
    assert names[0].startswith("0000000010")
    assert names[1].startswith("0000000100")


def test_upload_saves_fresh_on_failure(tmp_path):
    run, _ = fake_poster(status=503)
    with pytest.raises(UploaderError):
        upload(cfg(), [{"date": "2026-07-02", "model_id": "glm-5.2",
                        "input_tokens": 9, "output_tokens": 9}],
               tmp_path, poster=run, now_ts=200.0)
    pend = list_pending(tmp_path)
    assert len(pend) == 1
    body = json.loads(pend[0].read_text())
    assert body["points"][0]["input_tokens"] == 9


def test_upload_returns_points_sent(tmp_path):
    run, _ = fake_poster()
    pts = [{"date": "2026-07-02", "model_id": "glm-5.2", "input_tokens": 1, "output_tokens": 1}]
    n = upload(cfg(), pts, tmp_path, poster=run, now_ts=1.0)
    assert n == 1


def test_upload_empty_pending_no_drain(tmp_path):
    run, received = fake_poster()
    upload(cfg(), [], tmp_path, poster=run, now_ts=1.0)
    assert len(received) == 1  # only the fresh post
