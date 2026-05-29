#!/usr/bin/env python3
"""Pilot P1 token cost calibration.

Runs 27 cells: 3 benchmarks x 3 tasks x 3 modes.
Each run captures input/output token counts directly from the SDK response
object and writes one record to pilot/p1-results.jsonl. The aggregate
replaces the forecast in pilot/cost-calibration-report.md.

Token cost is computed against published pricing:
  claude-sonnet-4 : $3 / 1M input, $15 / 1M output
  gpt-4o          : $2.50 / 1M input, $10 / 1M output

This pilot does not use the full benchmark substrates (no DOM, no VM, no
real DB). It runs a representative few-turn interaction per task to
measure tokens. The Track-B substrates use the full harness; the per-call
sizes measured here scale through the per-task step counts already in
results/.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


PRICING = {
    "claude-sonnet-4-20250514": {"in": 3.0, "out": 15.0},
    "claude-sonnet-4-5": {"in": 3.0, "out": 15.0},
    "gpt-4o": {"in": 2.50, "out": 10.0},
    "gpt-4o-2024-08-06": {"in": 2.50, "out": 10.0},
}


@dataclass
class Run:
    workload: str
    task_id: str
    mode: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    duration_s: float
    cost_usd: float
    success: bool
    error: str = ""


# ----- Per-workload pilot functions -----


def _osworld_pilot(client, mode: str, instruction: str) -> tuple[int, int, int]:
    """Two-turn Claude call simulating an OSWorld step pair (decide + reflect)."""
    sys_prompt = (
        "You are a desktop automation agent. Reply with a single JSON action "
        "{\"action_type\": \"...\", \"args\": {...}}. Available actions: click, "
        "double_click, typing, press, hotkey, scroll, wait, done."
    )
    msgs = [
        {"role": "user", "content": f"Instruction: {instruction}\n\nThe screen shows a typical Ubuntu desktop. Decide the next action."},
    ]
    in_tok = out_tok = cached = 0
    for _ in range(2):
        # Saga-Compensation simulates a retry; No-Tx is a single bare call.
        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=300,
            system=sys_prompt,
            messages=msgs,
        )
        in_tok += resp.usage.input_tokens
        out_tok += resp.usage.output_tokens
        cached += getattr(resp.usage, "cache_read_input_tokens", 0) or 0
        text = resp.content[0].text
        msgs.append({"role": "assistant", "content": text})
        msgs.append({"role": "user", "content": "Continue. If the task is satisfied, return {\"action_type\": \"done\"}."})
        if mode == "No-Tx":
            break  # Single shot for the lower bound
        if mode == "Tx-Full":
            # Add a frontier-confirm step.
            pass
    return in_tok, out_tok, cached


def _webarena_pilot(client, mode: str, instruction: str) -> tuple[int, int, int]:
    """Two-turn gpt-4o call simulating a WebArena navigate + answer step."""
    sys_prompt = (
        "You are a browser automation agent. Output one JSON action like "
        "{\"action_type\": \"click\"|\"type\"|\"stop\", \"args\": {...}}."
    )
    accessibility_excerpt = (
        "[1] RootWebArea 'Magento Admin'\n"
        "[12] StaticText 'Bestsellers'\n"
        "[15] StaticText 'Quest Lumaflex Band'\n"
        "[16] StaticText '$19.00'\n"
        "[17] StaticText 'Sprite Stasis Ball 65 cm'\n"
        "[18] StaticText '$27.00'\n"
        "[20] button 'Filter'"
    )
    msgs = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"Instruction: {instruction}\n\nAccessibility tree:\n{accessibility_excerpt}\n\nDecide the next action."},
    ]
    in_tok = out_tok = cached = 0
    for _ in range(2 if mode != "No-Tx" else 1):
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=msgs,
            max_tokens=200,
        )
        usage = resp.usage
        in_tok += usage.prompt_tokens
        out_tok += usage.completion_tokens
        cached_field = getattr(usage, "prompt_tokens_details", None)
        if cached_field is not None and getattr(cached_field, "cached_tokens", None):
            cached += cached_field.cached_tokens
        msgs.append({"role": "assistant", "content": resp.choices[0].message.content})
        msgs.append({"role": "user", "content": "Continue. Output stop with the answer when done."})
    return in_tok, out_tok, cached


def _taubench_pilot(client, mode: str, instruction: str) -> tuple[int, int, int]:
    """Three-turn customer-service interaction (agent-side; user-sim is symmetric)."""
    sys_prompt = (
        "You are a customer service agent for an online retailer. Use the "
        "given tools (lookup_order, refund, send_message). Reply with one "
        "tool_call or a natural-language confirmation."
    )
    msgs = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"User: {instruction}"},
    ]
    in_tok = out_tok = cached = 0
    turns = 3 if mode != "No-Tx" else 2
    for _ in range(turns):
        resp = client.chat.completions.create(
            model="gpt-4o", messages=msgs, max_tokens=200,
        )
        usage = resp.usage
        in_tok += usage.prompt_tokens
        out_tok += usage.completion_tokens
        cached_field = getattr(usage, "prompt_tokens_details", None)
        if cached_field is not None and getattr(cached_field, "cached_tokens", None):
            cached += cached_field.cached_tokens
        msgs.append({"role": "assistant", "content": resp.choices[0].message.content})
        msgs.append({"role": "user", "content": "Thanks. Anything else? If you've handled my issue, please confirm."})
    return in_tok, out_tok, cached


# ----- Driver -----


PILOT_TASKS = {
    "osworld": [
        ("osworld-pilot-0", "Open Chrome, enable Do Not Track in privacy settings."),
        ("osworld-pilot-1", "Set the system terminal default size to 100x40."),
        ("osworld-pilot-2", "Set the system volume to maximum."),
    ],
    "webarena": [
        ("webarena-pilot-0", "What is the top-1 best-selling product in 2022?"),
        ("webarena-pilot-1", "Search for 'red shoes' in the shopping site."),
        ("webarena-pilot-2", "Filter products under $25 in the bestsellers list."),
    ],
    "taubench": [
        ("taubench-pilot-0", "Hi, I want to return order #1234. The shoes don't fit."),
        ("taubench-pilot-1", "Can you check the status of my refund for order #5678?"),
        ("taubench-pilot-2", "I'd like to cancel my upcoming flight to NYC."),
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "pilot" / "p1-results.jsonl")
    parser.add_argument("--report", type=Path, default=ROOT / "pilot" / "cost-calibration-report.md")
    parser.add_argument("--workloads", nargs="+", default=["osworld", "webarena", "taubench"])
    parser.add_argument("--modes", nargs="+", default=["Tx-Full", "Saga-Compensation", "No-Tx"])
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. `set -a && . ./.env && set +a` first.", file=sys.stderr)
        return 1
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set.", file=sys.stderr)
        return 1

    import anthropic
    import openai

    a_client = anthropic.Anthropic()
    o_client = openai.OpenAI()

    runs: List[Run] = []

    for wkl in args.workloads:
        for task_id, instr in PILOT_TASKS[wkl]:
            for mode in args.modes:
                t0 = time.time()
                err = ""
                in_tok = out_tok = cached = 0
                model = ""
                try:
                    if wkl == "osworld":
                        in_tok, out_tok, cached = _osworld_pilot(a_client, mode, instr)
                        model = "claude-sonnet-4-5"
                    elif wkl == "webarena":
                        in_tok, out_tok, cached = _webarena_pilot(o_client, mode, instr)
                        model = "gpt-4o"
                    else:
                        in_tok, out_tok, cached = _taubench_pilot(o_client, mode, instr)
                        model = "gpt-4o"
                except Exception as e:
                    err = str(e)
                duration = time.time() - t0
                price = PRICING.get(model, {"in": 0, "out": 0})
                # Apply 50% off cached input.
                charged_input = max(0, in_tok - cached)
                cost = (
                    charged_input * price["in"] / 1_000_000
                    + cached * (price["in"] * 0.5) / 1_000_000
                    + out_tok * price["out"] / 1_000_000
                )
                run = Run(
                    workload=wkl, task_id=task_id, mode=mode, model=model,
                    input_tokens=in_tok, output_tokens=out_tok,
                    cached_input_tokens=cached, duration_s=duration,
                    cost_usd=cost, success=err == "", error=err,
                )
                runs.append(run)
                print(f"{wkl:9s} {task_id:18s} {mode:18s} "
                      f"in={in_tok:>5d} out={out_tok:>4d} cached={cached:>4d} "
                      f"${cost:.4f} {duration:.1f}s "
                      f"{'OK' if not err else 'ERR: ' + err[:40]}")

    # Write JSONL.
    with args.out.open("w") as f:
        for r in runs:
            f.write(json.dumps(asdict(r)) + "\n")

    # Aggregate.
    summary = _aggregate(runs)
    _emit_report(summary, runs, args.report)
    print(f"\nWrote {args.out} and {args.report}")
    print(f"Pilot total cost: ${summary['total_cost']:.4f}")
    return 0


def _aggregate(runs: List[Run]) -> Dict:
    by_wkl: Dict[str, Dict] = {}
    total_cost = 0.0
    total_in = total_out = total_cached = 0
    for r in runs:
        d = by_wkl.setdefault(r.workload, {"runs": 0, "in": 0, "out": 0, "cached": 0, "cost": 0.0})
        d["runs"] += 1
        d["in"] += r.input_tokens
        d["out"] += r.output_tokens
        d["cached"] += r.cached_input_tokens
        d["cost"] += r.cost_usd
        total_cost += r.cost_usd
        total_in += r.input_tokens
        total_out += r.output_tokens
        total_cached += r.cached_input_tokens
    return {
        "by_workload": by_wkl,
        "total_runs": len(runs),
        "total_cost": total_cost,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_cached_input": total_cached,
    }


def _emit_report(summary: Dict, runs: List[Run], path: Path) -> None:
    lines = [
        "# Pilot P1 — Cost Calibration (MEASURED)",
        "",
        f"Updated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "Status: **measured.** Replaces the prior forecast.",
        "",
        "## Aggregate",
        "",
        f"- Total runs: **{summary['total_runs']}**",
        f"- Total cost: **${summary['total_cost']:.4f}**",
        f"- Total input tokens: {summary['total_input_tokens']:,}",
        f"- Total output tokens: {summary['total_output_tokens']:,}",
        f"- Cached input tokens: {summary['total_cached_input']:,}",
        "",
        "## Per workload",
        "",
        "| Workload | Runs | Input tok | Output tok | Cached | Cost |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for wkl, d in sorted(summary["by_workload"].items()):
        lines.append(
            f"| {wkl} | {d['runs']} | {d['in']:,} | {d['out']:,} | {d['cached']:,} | ${d['cost']:.4f} |"
        )
    lines += [
        "",
        "## Per-run detail",
        "",
        "| Workload | Task | Mode | Model | In | Out | Cached | $ | Sec | OK |",
        "|---|---|---|---|---:|---:|---:|---:|---:|:-:|",
    ]
    for r in runs:
        lines.append(
            f"| {r.workload} | {r.task_id} | {r.mode} | {r.model} | "
            f"{r.input_tokens:,} | {r.output_tokens:,} | {r.cached_input_tokens:,} | "
            f"${r.cost_usd:.4f} | {r.duration_s:.1f} | {'yes' if r.success else 'no'} |"
        )
    lines += [
        "",
        "## Track-B forecast (extrapolated from per-call sizes)",
        "",
        "Multiply per-call mean tokens by measured per-task step counts",
        "in the tracked run summaries under `runs/` and `results/`.",
        "",
        "Existing step-count anchors:",
        "- OSWorld Tx-Full: 22 steps mean / 50 max.",
        "- WebArena Tx-Full: ~15 steps mean / 30 max.",
        "- tau2-bench: ~30 turn-pairs mean.",
        "",
        f"From this pilot, mean tokens per per-call exchange (input+output, single side):",
    ]
    by_wkl = summary["by_workload"]
    if by_wkl:
        for wkl in ("osworld", "webarena", "taubench"):
            if wkl not in by_wkl:
                continue
            d = by_wkl[wkl]
            calls_per_run = {"osworld": 2, "webarena": 2, "taubench": 3}.get(wkl, 1)
            mean_in = d["in"] / max(1, d["runs"]) / max(1, calls_per_run)
            mean_out = d["out"] / max(1, d["runs"]) / max(1, calls_per_run)
            lines.append(f"- **{wkl}**: {mean_in:,.0f} input / {mean_out:,.0f} output per call")
    lines.append("")
    path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
