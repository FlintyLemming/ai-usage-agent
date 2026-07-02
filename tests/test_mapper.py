from reporter.mapper import map_points


def test_flatten_three_clients_one_day(tokscale_sample):
    points = map_points(tokscale_sample)
    # 2026-07-02: glm-5.2 (merged from zcode+opencode), claude-sonnet-4-5
    # 2026-07-01: glm-5.2
    by_key = {(p["date"], p["model_id"]): p for p in points}
    assert set(by_key) == {
        ("2026-07-02", "glm-5.2"),
        ("2026-07-02", "claude-sonnet-4-5"),
        ("2026-07-01", "glm-5.2"),
    }


def test_merge_duplicate_across_clients(tokscale_sample):
    points = map_points(tokscale_sample)
    p = next(p for p in points if p["date"] == "2026-07-02" and p["model_id"] == "glm-5.2")
    # zcode input 1_200_000 + opencode input 300_000 = 1_500_000
    # zcode output 450_000 + opencode output 140_000 = 590_000
    assert p["input_tokens"] == 1_500_000
    assert p["output_tokens"] == 590_000


def test_drops_cache_reasoning_cost_messages(tokscale_sample):
    points = map_points(tokscale_sample)
    for p in points:
        assert set(p.keys()) == {"date", "model_id", "input_tokens", "output_tokens"}


def test_raw_model_id_no_aliasing(tokscale_sample):
    points = map_points(tokscale_sample)
    ids = {p["model_id"] for p in points}
    assert "claude-sonnet-4-5" in ids
    assert "glm-5.2" in ids


def test_empty_contributions():
    assert map_points({"contributions": []}) == []
    assert map_points({}) == []


def test_missing_token_fields_default_zero():
    data = {"contributions": [{"date": "2026-07-02", "clients": [
        {"client": "zcode", "modelId": "glm-5.2", "tokens": {}},
    ]}]}
    pts = map_points(data)
    assert pts == [{"date": "2026-07-02", "model_id": "glm-5.2", "input_tokens": 0, "output_tokens": 0}]


def test_missing_input_key_uses_get_default():
    data = {"contributions": [{"date": "2026-07-02", "clients": [
        {"client": "zcode", "modelId": "glm-5.2", "tokens": {"output": 100}},
    ]}]}
    pts = map_points(data)
    assert pts[0]["input_tokens"] == 0
    assert pts[0]["output_tokens"] == 100
