#!/usr/bin/env python3
"""Overhead and speculation commit-latency benchmarks.

1. Overhead: wall-clock time for 10-step tasks at fp=0 across modes.
2. Speculation latency: time from first branch to winner commit vs K.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Set

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atomix.adapters import ToolAdapter
from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.runtime import AtomixRuntime
from atomix.tool_result import ToolMeta, normalize_tool_result


# ---------------------------------------------------------------------------
# Shared adapters (reused from fault recovery and speculation benchmarks)
# ---------------------------------------------------------------------------

class TaskState:
    def __init__(self) -> None:
        self.resources: Dict[str, str] = {}

    def write(self, key: str, value: str) -> None:
        self.resources[key] = value

    def delete(self, key: str) -> None:
        self.resources.pop(key, None)


class StepAdapter(ToolAdapter):
    name = "task_step"

    def scopes(self, args: Dict[str, Any]) -> Set[str]:
        return {f"resource:{args['key']}"}

    def to_effect(self, args: Dict[str, Any], result: Any, epoch: Epoch) -> Effect:
        key = str(args["key"])
        value = str(args["value"])
        state: TaskState = args["_state"]

        def compensation() -> None:
            state.delete(key)

        return Effect(
            description=f"step:{key}={value}",
            scopes={f"resource:{key}"},
            payload={"key": key, "value": value},
            idempotency_key=f"{epoch.trace_id}:{epoch.value}:{key}",
            compensation=compensation,
        )


class ObjectStore:
    def __init__(self) -> None:
        self.objects: Dict[str, str] = {}

    def create(self, object_id: str, value: str) -> None:
        self.objects[object_id] = value

    def delete(self, object_id: str) -> None:
        self.objects.pop(object_id, None)


class ObjectCreateAdapter(ToolAdapter):
    name = "object_create"

    def scopes(self, args: Dict[str, Any]) -> Set[str]:
        return {f"obj:{args['object_id']}"}

    def to_effect(self, args: Dict[str, Any], result: Any, epoch: Epoch) -> Effect:
        object_id = str(args["object_id"])
        value = str(args["value"])

        def compensation() -> None:
            args["store"].delete(object_id)

        return Effect(
            description=f"object:{object_id}",
            scopes={f"obj:{object_id}"},
            payload={"object_id": object_id, "value": value},
            idempotency_key=f"obj:{object_id}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
            compensation=compensation,
        )


# ---------------------------------------------------------------------------
# Benchmark 1: Overhead (fp=0, 10 steps, 1000 runs)
# ---------------------------------------------------------------------------

def _run_overhead_task(n_steps: int, mode: str, trace_id: str) -> None:
    state = TaskState()
    runtime = AtomixRuntime(
        apply_effect=lambda eff: state.write(
            str(eff.payload["key"]) if isinstance(eff.payload, dict) else "",
            str(eff.payload["value"]) if isinstance(eff.payload, dict) else "",
        ),
        effect_log_path=None,
        frontier_enabled=(mode == "Tx-Full"),
    )
    runtime.register_adapter("task_step", StepAdapter())

    for step_idx in range(n_steps):
        args = {"key": f"r{step_idx}", "value": f"v{step_idx}", "_state": state}
        epoch = runtime.epochs.next(trace_id=trace_id)

        if mode == "No-Tx":
            state.write(args["key"], args["value"])
        else:
            runtime.run_tool("task_step", lambda **kw: kw, args, epoch)


def benchmark_overhead(n_steps: int = 10, n_runs: int = 1000) -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    for mode in ["Tx-Full", "No-Frontier", "No-Tx"]:
        start = time.perf_counter()
        for i in range(n_runs):
            _run_overhead_task(n_steps, mode, f"overhead-{mode}-{i}")
        elapsed = time.perf_counter() - start
        per_task_us = (elapsed / n_runs) * 1e6
        per_step_us = per_task_us / n_steps
        results[mode] = {
            "total_s": round(elapsed, 4),
            "per_task_us": round(per_task_us, 1),
            "per_step_us": round(per_step_us, 1),
            "n_runs": n_runs,
            "n_steps": n_steps,
        }
    # Compute overhead ratio
    base = results["No-Tx"]["per_task_us"]
    for mode in results:
        results[mode]["overhead_vs_notx"] = round(
            results[mode]["per_task_us"] / base, 2
        )
    return results


# ---------------------------------------------------------------------------
# Benchmark 2: Speculation commit latency vs K
# ---------------------------------------------------------------------------

def _run_speculation_latency(k: int, trace_id: str) -> Dict[str, Any]:
    store = ObjectStore()
    runtime = AtomixRuntime(
        apply_effect=lambda eff: store.create(
            str(eff.payload["object_id"]) if isinstance(eff.payload, dict) else "",
            str(eff.payload["value"]) if isinstance(eff.payload, dict) else "",
        ),
        frontier_enabled=True,
    )
    runtime.register_adapter("object_create", ObjectCreateAdapter())

    branch_ids = [f"b{idx}" for idx in range(k)]
    branch_txs: Dict[str, Any] = {}
    branch_epochs: Dict[str, Epoch] = {}
    branch_args: Dict[str, Dict[str, Any]] = {}

    t_start = time.perf_counter()

    # Create all branches
    for branch in branch_ids:
        epoch = runtime.epochs.next(trace_id=trace_id, branch_id=branch)
        args = {
            "object_id": f"{branch}-obj",
            "value": f"value-{branch}",
            "store": store,
        }
        adapter = runtime.adapters.get("object_create")
        scopes = adapter.scopes(args)
        tx = runtime.tx_manager.begin(scopes, epoch)
        meta = ToolMeta(
            tool_name="object_create",
            trace_id=epoch.trace_id,
            branch_id=epoch.branch_id,
            attempt=0,
        )
        tool_result = normalize_tool_result(args, meta=meta)
        effect = adapter.to_effect(args, tool_result, epoch)
        runtime.tx_manager.record_effect(tx, effect)
        branch_txs[branch] = tx
        branch_epochs[branch] = epoch
        branch_args[branch] = args

    t_branches_created = time.perf_counter()

    # Select winner, abort losers, advance frontier, commit winner
    winner = random.choice(branch_ids)
    for branch in branch_ids:
        if branch != winner:
            runtime.tx_manager.abort(branch_txs[branch], "loser branch")

    scopes = runtime.adapters.get("object_create").scopes(branch_args[winner])
    runtime.advance_frontier(scopes, branch_epochs[winner])
    runtime.tx_manager.commit(branch_txs[winner])

    t_committed = time.perf_counter()

    return {
        "k": k,
        "branch_setup_us": round((t_branches_created - t_start) * 1e6, 1),
        "commit_us": round((t_committed - t_branches_created) * 1e6, 1),
        "total_us": round((t_committed - t_start) * 1e6, 1),
        "effects_buffered": k,
        "effects_committed": 1,
        "effects_aborted": k - 1,
    }


def benchmark_speculation_latency(
    k_values: List[int] | None = None, n_runs: int = 200
) -> Dict[int, Dict[str, Any]]:
    if k_values is None:
        k_values = [2, 4, 8, 16]

    results: Dict[int, Dict[str, Any]] = {}
    for k in k_values:
        branch_setup_us_all = []
        commit_us_all = []
        total_us_all = []

        for i in range(n_runs):
            r = _run_speculation_latency(k, f"spec-lat-{k}-{i}")
            branch_setup_us_all.append(r["branch_setup_us"])
            commit_us_all.append(r["commit_us"])
            total_us_all.append(r["total_us"])

        results[k] = {
            "k": k,
            "n_runs": n_runs,
            "mean_branch_setup_us": round(sum(branch_setup_us_all) / n_runs, 1),
            "mean_commit_us": round(sum(commit_us_all) / n_runs, 1),
            "mean_total_us": round(sum(total_us_all) / n_runs, 1),
            "p99_total_us": round(sorted(total_us_all)[int(n_runs * 0.99)], 1),
        }
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 60)
    print("Benchmark 1: Overhead (fp=0, 10 steps, 1000 runs)")
    print("=" * 60)
    overhead = benchmark_overhead()
    for mode, data in overhead.items():
        print(f"  {mode:15s}: {data['per_task_us']:8.1f} us/task  "
              f"({data['per_step_us']:6.1f} us/step)  "
              f"{data['overhead_vs_notx']:.2f}x vs No-Tx")

    print()
    print("=" * 60)
    print("Benchmark 2: Speculation Commit Latency vs K (200 runs)")
    print("=" * 60)
    latency = benchmark_speculation_latency()
    print(f"  {'K':>4s}  {'Setup(us)':>10s}  {'Commit(us)':>11s}  "
          f"{'Total(us)':>10s}  {'p99(us)':>10s}")
    for k, data in sorted(latency.items()):
        print(f"  {k:4d}  {data['mean_branch_setup_us']:10.1f}  "
              f"{data['mean_commit_us']:11.1f}  "
              f"{data['mean_total_us']:10.1f}  "
              f"{data['p99_total_us']:10.1f}")

    # Write combined results
    out = Path("/tmp/atomix_overhead_results.json")
    out.write_text(json.dumps({
        "overhead": overhead,
        "speculation_latency": {str(k): v for k, v in latency.items()},
    }, indent=2))
    print(f"\nWrote results to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
