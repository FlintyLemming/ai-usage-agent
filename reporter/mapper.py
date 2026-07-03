"""Flatten tokscale contributions into insight points (pure, no I/O).

Forwards all five token categories (input, output, cache_read, cache_write,
reasoning) so insight's total matches tokscale's totalTokens. Drops
cost/messages/client/providerId/activeTimeMs. Merges duplicate (date,
model_id) across clients by summing every token field.
"""
from __future__ import annotations


def map_points(tokscale_json: dict) -> list[dict]:
    """Return insight points[] from a parsed `tokscale graph` document.

    Each point: {"date","model_id","input_tokens","output_tokens",
    "cache_read_tokens","cache_write_tokens","reasoning_tokens"}.
    Duplicate (date, model_id) across clients within a day are merged by sum.
    """
    contributions = tokscale_json.get("contributions") or []
    merged: dict[tuple[str, str], dict] = {}
    for day in contributions:
        date = day.get("date")
        for src in day.get("clients") or []:
            model_id = src.get("modelId")
            if not date or not model_id:
                continue  # insight rejects the whole payload on a null key
            tokens = src.get("tokens") or {}
            # tokscale emits camelCase token keys (cacheRead, cacheWrite, reasoning)
            fields = {
                "input_tokens": int(tokens.get("input", 0) or 0),
                "output_tokens": int(tokens.get("output", 0) or 0),
                "cache_read_tokens": int(tokens.get("cacheRead", 0) or 0),
                "cache_write_tokens": int(tokens.get("cacheWrite", 0) or 0),
                "reasoning_tokens": int(tokens.get("reasoning", 0) or 0),
            }
            key = (date, model_id)
            if key in merged:
                for k, v in fields.items():
                    merged[key][k] += v
            else:
                merged[key] = {"date": date, "model_id": model_id, **fields}
    return list(merged.values())
