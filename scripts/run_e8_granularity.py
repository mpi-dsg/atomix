#!/usr/bin/env python3
"""E8: Resource-granularity throughput.

Synthetic hierarchical-scope workload, no LLM. Sweep overlap fraction
o ∈ {0, 0.25, 0.5, 0.75, 1.0}. 1,000 transactions per config, 8 agents,
30 task seeds.

Modes: Tx-Full (per-resource frontier), Tx-GlobalFrontier, Workflow-Lock
(single mutex), No-Tx (no scope tracking, just race).

Fills Tables tab:e8-granularity, tab:e8-granularity-tail.
Expected: at o=0, Tx-Full ≥80% of No-Tx and ≥2× Workflow-Lock. Global
frontier degrades disjoint throughput.

Output: runs/A7/E8/results.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


OVERLAPS = (0.0, 0.25, 0.5, 0.75, 1.0)
MODES = ("Tx-Full", "Tx-GlobalFrontier", "Workflow-Lock", "No-Tx")


def _simulate(
    mode: str, overlap: float, n_tx: int, n_agents: int, seed: int
) -> Dict:
    rng = random.Random(seed)
    # Each agent picks tx scopes from a small pool. Overlap fraction
    # controls how often two agents pick the same scope.
    scopes_pool = [f"r{i}" for i in range(n_agents * 4)]
    completed = 0
    blocked = 0
    aborted = 0
    # Throughput proxy: number of effects committed per unit "wall time".
    # Wall time = number of serial barriers (locks/frontier waits).
    wall_steps = 0
    waits: List[int] = []  # per-tx wait barriers
    t0 = time.perf_counter()
    # Per-resource last-committer epoch (for Tx-Full / GF).
    frontier: Dict[str, int] = defaultdict(lambda: -1)
    global_frontier = -1
    for tx_i in range(n_tx):
        # Decide whether to overlap with another agent's recent scope.
        if rng.random() < overlap:
            scope = rng.choice(scopes_pool[: max(1, len(scopes_pool) // 4)])
        else:
            scope = rng.choice(scopes_pool)
        epoch = tx_i
        if mode == "Tx-Full":
            # Commit when frontier[scope] >= epoch (always true here since
            # we run linearly, but we bump per-scope wall_steps when there
            # is contention with previous tx on same scope).
            if frontier[scope] >= 0 and tx_i - frontier[scope] < n_agents:
                wall_steps += 1
                waits.append(1)
            else:
                waits.append(0)
            frontier[scope] = epoch
            completed += 1
        elif mode == "Tx-GlobalFrontier":
            # Single global frontier: every commit serializes on it.
            wall_steps += 1
            waits.append(1)
            global_frontier = epoch
            completed += 1
        elif mode == "Workflow-Lock":
            # Single mutex around the workflow: every tx serializes.
            wall_steps += 1
            waits.append(1)
            completed += 1
        elif mode == "No-Tx":
            # Race; on overlap, abort one with prob 0.5.
            if rng.random() < overlap * 0.5:
                aborted += 1
            else:
                completed += 1
            waits.append(0)
        else:
            raise ValueError(mode)
    duration = time.perf_counter() - t0
    waits_sorted = sorted(waits)
    n_waits = len(waits_sorted)
    p95 = waits_sorted[int(0.95 * (n_waits - 1))] if n_waits else 0
    avg_wait = sum(waits) / max(1, n_waits)
    return {
        "mode": mode,
        "overlap": overlap,
        "n_tx": n_tx,
        "completed": completed,
        "aborted": aborted,
        "blocked": blocked,
        "wall_steps": wall_steps,
        "duration_s": duration,
        "throughput_ops_per_step": completed / max(1, wall_steps + 1),
        "avg_wait_barriers": avg_wait,
        "p95_wait_barriers": p95,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-tx", type=int, default=1000)
    parser.add_argument("--n-agents", type=int, default=8)
    parser.add_argument("--n-seeds", type=int, default=30)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "runs" / "A7" / "E8" / "results.json"
    )
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    cells: Dict[str, Dict] = {}
    for mode in MODES:
        for overlap in OVERLAPS:
            agg = []
            for seed in range(args.n_seeds):
                agg.append(_simulate(mode, overlap, args.n_tx, args.n_agents, seed))
            cell_key = f"{mode}|o={overlap}"
            cells[cell_key] = {
                "mode": mode,
                "overlap": overlap,
                "n_seeds": args.n_seeds,
                "mean_throughput": sum(r["throughput_ops_per_step"] for r in agg) / len(agg),
                "p99_wall_steps": sorted(r["wall_steps"] for r in agg)[
                    int(0.99 * (len(agg) - 1))
                ],
                "mean_aborts": sum(r["aborted"] for r in agg) / len(agg),
                "mean_avg_wait_barriers": sum(r["avg_wait_barriers"] for r in agg) / len(agg),
                "mean_p95_wait_barriers": sum(r["p95_wait_barriers"] for r in agg) / len(agg),
            }
    args.out.write_text(json.dumps({"cells": cells}, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
