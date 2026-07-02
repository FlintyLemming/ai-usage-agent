"""Flatten tokscale contributions into insight points (pure, no I/O).

Drops cache/cacheWrite/reasoning/cost/messages/client/providerId/activeTimeMs.
Merges duplicate (date, model_id) across clients by summing input/output.
"""
from __future__ import annotations


def map_points(tokscale_json: dict) -> list[dict]:
    """Return insight points[] from a parsed `tokscale graph` document.

    Each point: {"date","model_id","input_tokens","output_tokens"}.
    Duplicate (date, model_id) across clients within a day are merged by sum.
    """
    contributions = tokscale_json.get("contributions") or []
    merged: dict[tuple[str, str], dict] = {}
    for day in contributions:
        date = day.get("date")
        for src in day.get("clients") or []:
            model_id = src.get("modelId")
            tokens = src.get("tokens") or {}
            input_tokens = int(tokens.get("input", 0) or 0)
            output_tokens = int(tokens.get("output", 0) or 0)
            key = (date, model_id)
            if key in merged:
                merged[key]["input_tokens"] += input_tokens
                merged[key]["output_tokens"] += output_tokens
            else:
                merged[key] = {
                    "date": date,
                    "model_id": model_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }
    return list(merged.values())
