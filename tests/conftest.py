"""Shared fixtures. No network, no live tokscale."""
import json
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def tokscale_sample() -> dict:
    """Real captured `tokscale graph` output."""
    return json.loads((FIXTURES / "tokscale_graph_sample.json").read_text())


@pytest.fixture
def sample_config_dict() -> dict:
    return {
        "source_id": "macbook-flinty",
        "source_label": "MacBook Pro",
        "insight_url": "http://127.0.0.1:8765",
        "tokscale_bin": "npx",
        "tokscale_args": ["tokscale@latest", "graph"],
        "lookback_days": 90,
        "request_timeout_seconds": 30,
    }
