"""Cross-harness LLM usage capture.

Drop-in: import `record_usage(provider, model, input, output, cached, run_id)`
and the call lands in a JSONL file at `$ATOMIX_USAGE_LOG` (or
`runs/usage.jsonl` if unset). Aggregator reads the JSONL and reports
per-run / per-mode / per-benchmark cost.

Pricing table is centralized so updating one place updates every harness.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


PRICING = {
    # Anthropic
    "claude-sonnet-4-20250514": {"in": 3.0, "out": 15.0, "cached": 0.30},
    "claude-sonnet-4-5": {"in": 3.0, "out": 15.0, "cached": 0.30},
    "claude-haiku-4-5": {"in": 1.0, "out": 5.0, "cached": 0.10},
    # OpenAI
    "gpt-4o": {"in": 2.50, "out": 10.0, "cached": 1.25},
    "gpt-4o-2024-08-06": {"in": 2.50, "out": 10.0, "cached": 1.25},
    "gpt-4o-mini": {"in": 0.15, "out": 0.60, "cached": 0.075},
    "gpt-4.1": {"in": 2.00, "out": 8.00, "cached": 0.50},
    "gpt-4.1-mini": {"in": 0.40, "out": 1.60, "cached": 0.10},
}


@dataclass(frozen=True)
class UsageRecord:
    ts: float
    provider: str  # "anthropic" | "openai"
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float
    run_id: str


_lock = threading.Lock()
_path: Optional[Path] = None


def _resolve_path() -> Path:
    global _path
    if _path is not None:
        return _path
    p = os.environ.get("ATOMIX_USAGE_LOG")
    if p:
        _path = Path(p)
    else:
        _path = Path("runs/usage.jsonl")
    _path.parent.mkdir(parents=True, exist_ok=True)
    return _path


def compute_cost(model: str, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0) -> float:
    price = PRICING.get(model)
    if price is None:
        return 0.0
    charged_input = max(0, input_tokens - cached_input_tokens)
    return (
        charged_input * price["in"] / 1_000_000
        + cached_input_tokens * price["cached"] / 1_000_000
        + output_tokens * price["out"] / 1_000_000
    )


def record_usage(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    run_id: str = "",
) -> UsageRecord:
    rec = UsageRecord(
        ts=time.time(),
        provider=provider,
        model=model,
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        cached_input_tokens=int(cached_input_tokens),
        cost_usd=compute_cost(model, input_tokens, output_tokens, cached_input_tokens),
        run_id=run_id,
    )
    path = _resolve_path()
    with _lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(rec)) + "\n")
    return rec


def record_anthropic(response, *, model: str, run_id: str = "") -> UsageRecord:
    """Extract usage from an Anthropic SDK response and record it."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return record_usage(
            provider="anthropic", model=model,
            input_tokens=0, output_tokens=0, run_id=run_id,
        )
    return record_usage(
        provider="anthropic",
        model=model,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cached_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        run_id=run_id,
    )


def record_openai(response, *, model: str, run_id: str = "") -> UsageRecord:
    """Extract usage from an OpenAI ChatCompletion response and record it."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return record_usage(
            provider="openai", model=model,
            input_tokens=0, output_tokens=0, run_id=run_id,
        )
    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    return record_usage(
        provider="openai",
        model=model,
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        cached_input_tokens=cached,
        run_id=run_id,
    )


def aggregate(path: Optional[Path] = None) -> dict:
    """Read usage.jsonl and return totals by run_id / model / provider."""
    p = Path(path) if path else _resolve_path()
    if not p.exists():
        return {
            "total_records": 0,
            "total_cost": 0.0,
            "by_run_id": {},
            "by_model": {},
            "by_provider": {},
        }
    by_run: dict = {}
    by_model: dict = {}
    by_provider: dict = {}
    total_cost = 0.0
    n = 0
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            n += 1
            total_cost += d["cost_usd"]
            for grp, key in (
                (by_run, d.get("run_id", "")),
                (by_model, d["model"]),
                (by_provider, d["provider"]),
            ):
                e = grp.setdefault(key, {"calls": 0, "input": 0, "output": 0, "cached": 0, "cost": 0.0})
                e["calls"] += 1
                e["input"] += d["input_tokens"]
                e["output"] += d["output_tokens"]
                e["cached"] += d["cached_input_tokens"]
                e["cost"] += d["cost_usd"]
    return {
        "total_records": n,
        "total_cost": total_cost,
        "by_run_id": by_run,
        "by_model": by_model,
        "by_provider": by_provider,
    }


def reset_path() -> None:
    """Test helper — re-resolve from env on next call."""
    global _path
    _path = None
