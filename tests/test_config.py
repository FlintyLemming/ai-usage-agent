import json
from pathlib import Path

import pytest

from reporter.config import (
    Config,
    ConfigError,
    default_config_path,
    default_state_dir,
    load_config,
    write_template_config,
)


def write_cfg(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return p


def test_load_valid_config(tmp_path, sample_config_dict):
    cfg = load_config(write_cfg(tmp_path, sample_config_dict))
    assert cfg.source_id == "macbook-flinty"
    assert cfg.source_label == "MacBook Pro"
    assert cfg.insight_url == "http://127.0.0.1:8765"
    assert cfg.tokscale_bin == "tokscale"
    assert cfg.tokscale_args == ["graph"]
    assert cfg.lookback_days == 90
    assert cfg.request_timeout_seconds == 30
    assert cfg.auth_token is None


def test_missing_source_id_exits_2(tmp_path, sample_config_dict):
    del sample_config_dict["source_id"]
    with pytest.raises(ConfigError) as exc:
        load_config(write_cfg(tmp_path, sample_config_dict))
    assert exc.value.exit_code == 2
    assert "source_id" in str(exc.value).lower()


def test_empty_source_id_exits_2(tmp_path, sample_config_dict):
    sample_config_dict["source_id"] = ""
    with pytest.raises(ConfigError) as exc:
        load_config(write_cfg(tmp_path, sample_config_dict))
    assert exc.value.exit_code == 2


def test_missing_insight_url_exits_2(tmp_path, sample_config_dict):
    del sample_config_dict["insight_url"]
    with pytest.raises(ConfigError) as exc:
        load_config(write_cfg(tmp_path, sample_config_dict))
    assert exc.value.exit_code == 2


def test_unknown_keys_ignored(tmp_path, sample_config_dict):
    sample_config_dict["future_field"] = "ignored"
    cfg = load_config(write_cfg(tmp_path, sample_config_dict))
    assert cfg.source_id == "macbook-flinty"


def test_defaults_applied(tmp_path, sample_config_dict):
    # only required fields
    minimal = {"source_id": "x", "insight_url": "http://localhost:8765"}
    cfg = load_config(write_cfg(tmp_path, minimal))
    assert cfg.source_label is None
    assert cfg.tokscale_bin == "tokscale"
    assert cfg.tokscale_args == ["graph"]
    assert cfg.lookback_days == 90
    assert cfg.request_timeout_seconds == 30


def test_bad_lookback_days_exits_2(tmp_path, sample_config_dict):
    sample_config_dict["lookback_days"] = 0
    with pytest.raises(ConfigError) as exc:
        load_config(write_cfg(tmp_path, sample_config_dict))
    assert exc.value.exit_code == 2


def test_auth_token_optional_and_kept(tmp_path, sample_config_dict):
    sample_config_dict["auth_token"] = "secret"
    cfg = load_config(write_cfg(tmp_path, sample_config_dict))
    assert cfg.auth_token == "secret"


def test_write_template_prefills_hostname(tmp_path):
    p = tmp_path / "config.json"
    out = write_template_config(p, "macbook-flinty")
    assert out == p
    data = json.loads(p.read_text())
    assert data["source_id"] == "<macbook-flinty>"
    assert data["insight_url"] == "http://127.0.0.1:8765"


def test_default_config_path_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_config_path() == tmp_path / "ai-usage-reporter" / "config.json"


def test_default_state_dir_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_state_dir() == tmp_path / "ai-usage-reporter"
