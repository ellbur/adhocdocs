"""What a run's API requests cost, from each response's usage and pricing.toml."""

import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def prices(path: Path = HERE / "pricing.toml") -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def model_prices(p: dict, model: str) -> dict | None:
    keys = [k for k in p["models"] if model.startswith(k)]
    return p["models"][max(keys, key=len)] if keys else None


def request_cost(p: dict, model: str, usage: dict) -> float | None:
    """Dollars for one request, or None for a model pricing.toml doesn't list."""
    m = model_prices(p, model)
    if m is None:
        return None
    inp = usage.get("input_tokens") or 0
    read = usage.get("cache_read_input_tokens") or 0
    written = usage.get("cache_creation_input_tokens") or 0
    by_ttl = usage.get("cache_creation") or {}
    w1h = by_ttl.get("ephemeral_1h_input_tokens") or 0
    w5m = by_ttl.get("ephemeral_5m_input_tokens", written - w1h) or 0
    out = usage.get("output_tokens") or 0
    pin, pout = m["input"], m["output"]
    if "long_prompt" in m and inp + read + written > m["long_prompt"]:
        pin, pout = m["long_input"], m["long_output"]
    dollars = (inp * pin + read * m["cache_read"] + w5m * pin * p["cache_write_5m"]
               + w1h * pin * p["cache_write_1h"] + out * pout)
    return dollars / 1e6


def summarize(requests: list[dict], p: dict | None = None) -> dict:
    """Tokens and dollars by model, and in all. Each request: {"model", "usage"}."""
    p = p or prices()
    by = {}
    unpriced = set()
    for r in requests:
        model, u = r.get("model") or "?", r.get("usage") or {}
        b = by.setdefault(model, {"requests": 0, "input_tokens": 0, "cache_read_input_tokens": 0,
                                  "cache_creation_input_tokens": 0, "output_tokens": 0, "dollars": 0.0})
        b["requests"] += 1
        for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"):
            b[k] += u.get(k) or 0
        c = request_cost(p, model, u)
        if c is None:
            unpriced.add(model)
        else:
            b["dollars"] += c
    for b in by.values():
        b["dollars"] = round(b["dollars"], 4)
    return {"prices_as_of": p["as_of"], "by_model": by,
            "dollars": round(sum(b["dollars"] for b in by.values()), 4),
            "unpriced_models": sorted(unpriced)}
