#!/usr/bin/env python3
"""E3.7: Multi-Agent Contention Benchmark.

Extended contention patterns with variable agent counts and latency metrics.
No LLM calls - purely synthetic async workload.

Patterns:
1. Disjoint writes: Each agent writes its own file (no contention expected).
2. Counter scaling: N agents increment a shared counter.
3. Read-write conflict: Reader vs writer on shared state.
4. Structured write-write: Two agents do read-modify-write on shared JSON.

Modes:
- Tx-Full: asyncio.Lock serializes all ops (simulates frontier gating).
- Mutex-Per-Resource: per-resource lock on individual operations.
- OCC: optimistic concurrency control with version checking (read version,
       check-and-increment on write; reject if stale).
- No-Frontier: No lock, race-prone concurrent access.
- No-Tx: Same as No-Frontier (both racy for synthetic tests).

Usage:
    python run_multiagent_contention.py --runs 10
    python run_multiagent_contention.py --runs 100 --output results/e3_contention_full.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DisjointFiles:
    """Each agent writes to its own key. No cross-agent contention."""
    files: Dict[str, int] = field(default_factory=dict)

@dataclass
class SharedCounter:
    """Shared counter for N-agent increment."""
    value: int = 0

@dataclass
class ReadWriteState:
    """Shared state with committed/uncommitted tracking."""
    committed_value: int = 0
    uncommitted_value: Optional[int] = None
    dirty_reads: int = 0

@dataclass
class SharedJSON:
    """Shared JSON document for structured write-write."""
    data: Dict[str, Any] = field(default_factory=dict)
    corruption_detected: bool = False


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@dataclass
class RunMetrics:
    correct: bool
    ops_count: int
    elapsed_s: float
    op_latencies_us: List[float] = field(default_factory=list)

    @property
    def throughput_ops_sec(self) -> float:
        return self.ops_count / self.elapsed_s if self.elapsed_s > 0 else 0

    @property
    def mean_latency_us(self) -> float:
        if not self.op_latencies_us:
            return 0.0
        return statistics.mean(self.op_latencies_us)

    @property
    def p50_latency_us(self) -> float:
        if not self.op_latencies_us:
            return 0.0
        return statistics.median(self.op_latencies_us)

    @property
    def p99_latency_us(self) -> float:
        if not self.op_latencies_us:
            return 0.0
        s = sorted(self.op_latencies_us)
        idx = int(len(s) * 0.99)
        return s[min(idx, len(s) - 1)]


def aggregate_metrics(runs: List[RunMetrics]) -> Dict[str, Any]:
    """Aggregate RunMetrics across multiple runs."""
    correct_count = sum(1 for r in runs if r.correct)
    throughputs = [r.throughput_ops_sec for r in runs]
    mean_lats = [r.mean_latency_us for r in runs if r.op_latencies_us]
    p99_lats = [r.p99_latency_us for r in runs if r.op_latencies_us]

    def stats(vals: List[float]) -> Dict[str, float]:
        if not vals:
            return {"mean": 0, "p50": 0, "p99": 0}
        s = sorted(vals)
        return {
            "mean": round(statistics.mean(s), 2),
            "p50": round(statistics.median(s), 2),
            "p99": round(s[min(int(len(s) * 0.99), len(s) - 1)], 2),
        }

    return {
        "runs": len(runs),
        "correctness_rate": round(correct_count / len(runs), 4),
        "throughput_ops_sec": stats(throughputs),
        "latency_us": {
            "mean": stats(mean_lats),
            "p99": stats(p99_lats),
        },
    }


# ---------------------------------------------------------------------------
# Pattern 1: Disjoint writes
# ---------------------------------------------------------------------------

async def run_disjoint_writes(
    mode: str, n_agents: int, ops_per_agent: int = 50,
) -> RunMetrics:
    """Each agent increments its own counter. No contention expected."""
    state = DisjointFiles()
    for i in range(n_agents):
        state.files[f"agent_{i}"] = 0

    global_lock = asyncio.Lock()
    per_resource = PerResourceLocks()
    occ = AsyncOCCVersionControl()
    latencies: List[float] = []
    total_ops = n_agents * ops_per_agent

    async def agent_fn(agent_id: int) -> None:
        key = f"agent_{agent_id}"
        for _ in range(ops_per_agent):
            t0 = time.perf_counter_ns()
            if mode == "Tx-Full":
                async with global_lock:
                    state.files[key] += 1
                    await asyncio.sleep(random.uniform(0.00001, 0.0001))
            elif mode == "Mutex-Per-Resource":
                async with per_resource.lock_for(key):
                    state.files[key] += 1
                    await asyncio.sleep(random.uniform(0.00001, 0.0001))
            elif mode == "OCC":
                # Disjoint: each agent writes its own key, so OCC never conflicts
                ver = await occ.read_version(key)
                await asyncio.sleep(random.uniform(0.00001, 0.0001))
                if await occ.try_commit(key, ver):
                    state.files[key] += 1
                # else: lost update (shouldn't happen for disjoint)
            else:
                state.files[key] += 1
                await asyncio.sleep(random.uniform(0.00001, 0.0001))
            lat = (time.perf_counter_ns() - t0) / 1000.0
            latencies.append(lat)

    t_start = time.perf_counter()
    await asyncio.gather(*(agent_fn(i) for i in range(n_agents)))
    elapsed = time.perf_counter() - t_start

    correct = all(state.files[f"agent_{i}"] == ops_per_agent for i in range(n_agents))
    return RunMetrics(
        correct=correct,
        ops_count=total_ops,
        elapsed_s=elapsed,
        op_latencies_us=latencies,
    )


# ---------------------------------------------------------------------------
# Pattern 2: Counter scaling (N agents)
# ---------------------------------------------------------------------------

async def run_counter_scaling(
    mode: str, n_agents: int, ops_per_agent: int = 50,
) -> RunMetrics:
    """N agents increment a shared counter. Tx-Full should always be correct."""
    counter = SharedCounter()
    global_lock = asyncio.Lock()
    per_resource = PerResourceLocks()
    occ = AsyncOCCVersionControl()
    latencies: List[float] = []
    total_ops = n_agents * ops_per_agent

    async def agent_fn(agent_id: int) -> None:
        for _ in range(ops_per_agent):
            t0 = time.perf_counter_ns()
            if mode == "Tx-Full":
                async with global_lock:
                    old = counter.value
                    await asyncio.sleep(random.uniform(0.0001, 0.001))
                    counter.value = old + 1
            elif mode == "Mutex-Per-Resource":
                async with per_resource.lock_for("shared_counter"):
                    old = counter.value
                    await asyncio.sleep(random.uniform(0.0001, 0.001))
                    counter.value = old + 1
            elif mode == "OCC":
                ver = await occ.read_version("shared_counter")
                old = counter.value
                await asyncio.sleep(random.uniform(0.0001, 0.001))
                if await occ.try_commit("shared_counter", ver):
                    counter.value = old + 1
                # else: stale -- increment lost (optimistic abort)
            else:
                old = counter.value
                await asyncio.sleep(random.uniform(0.0001, 0.001))
                counter.value = old + 1
            lat = (time.perf_counter_ns() - t0) / 1000.0
            latencies.append(lat)

    t_start = time.perf_counter()
    await asyncio.gather(*(agent_fn(i) for i in range(n_agents)))
    elapsed = time.perf_counter() - t_start

    expected = n_agents * ops_per_agent
    correct = counter.value == expected
    return RunMetrics(
        correct=correct,
        ops_count=total_ops,
        elapsed_s=elapsed,
        op_latencies_us=latencies,
    )


# ---------------------------------------------------------------------------
# Pattern 3: Read-write conflict
# ---------------------------------------------------------------------------

async def run_read_write_conflict(
    mode: str, ops_per_agent: int = 50,
) -> RunMetrics:
    """Agent A writes (set uncommitted, sleep, commit). Agent B reads.
    Under Tx-Full, B never sees uncommitted state."""
    state = ReadWriteState()
    global_lock = asyncio.Lock()
    per_resource = PerResourceLocks()
    occ = AsyncOCCVersionControl()
    latencies: List[float] = []
    total_ops = ops_per_agent * 2  # writer + reader

    async def writer() -> None:
        for i in range(1, ops_per_agent + 1):
            t0 = time.perf_counter_ns()
            if mode in ("Tx-Full", "Mutex-Per-Resource"):
                lk = global_lock if mode == "Tx-Full" else per_resource.lock_for("shared_state")
                async with lk:
                    state.uncommitted_value = i
                    await asyncio.sleep(random.uniform(0.0001, 0.001))
                    state.committed_value = i
                    state.uncommitted_value = None
            elif mode == "OCC":
                # OCC writer: read version, set uncommitted, sleep, commit
                ver = await occ.read_version("shared_state")
                state.uncommitted_value = i
                await asyncio.sleep(random.uniform(0.0001, 0.001))
                if await occ.try_commit("shared_state", ver):
                    state.committed_value = i
                state.uncommitted_value = None
            else:
                state.uncommitted_value = i
                await asyncio.sleep(random.uniform(0.0001, 0.001))
                state.committed_value = i
                state.uncommitted_value = None
            lat = (time.perf_counter_ns() - t0) / 1000.0
            latencies.append(lat)

    async def reader() -> None:
        for _ in range(ops_per_agent):
            t0 = time.perf_counter_ns()
            if mode in ("Tx-Full", "Mutex-Per-Resource"):
                lk = global_lock if mode == "Tx-Full" else per_resource.lock_for("shared_state")
                async with lk:
                    val = state.committed_value
                    is_dirty = state.uncommitted_value is not None
                    await asyncio.sleep(random.uniform(0.00005, 0.0005))
            elif mode == "OCC":
                # OCC reader: reads are non-blocking (optimistic); dirty reads
                # can still happen because OCC only gates writes, not reads
                val = state.committed_value
                is_dirty = state.uncommitted_value is not None
                await asyncio.sleep(random.uniform(0.00005, 0.0005))
            else:
                val = state.committed_value
                is_dirty = state.uncommitted_value is not None
                await asyncio.sleep(random.uniform(0.00005, 0.0005))
            if is_dirty:
                state.dirty_reads += 1
            lat = (time.perf_counter_ns() - t0) / 1000.0
            latencies.append(lat)

    t_start = time.perf_counter()
    await asyncio.gather(writer(), reader())
    elapsed = time.perf_counter() - t_start

    correct = state.dirty_reads == 0
    return RunMetrics(
        correct=correct,
        ops_count=total_ops,
        elapsed_s=elapsed,
        op_latencies_us=latencies,
    )


# ---------------------------------------------------------------------------
# Pattern 4: Structured write-write
# ---------------------------------------------------------------------------

async def run_structured_write_write(
    mode: str, ops_per_agent: int = 50,
) -> RunMetrics:
    """Two agents do read-modify-write on a shared JSON dict.
    A updates key_A, B updates key_B. Without lock, the read-modify-write
    cycle can overwrite the other agent's key (lost update)."""
    state = SharedJSON(data={"key_A": 0, "key_B": 0})
    global_lock = asyncio.Lock()
    per_resource = PerResourceLocks()
    occ = AsyncOCCVersionControl()
    latencies: List[float] = []
    total_ops = ops_per_agent * 2

    async def agent_a() -> None:
        for _ in range(ops_per_agent):
            t0 = time.perf_counter_ns()
            if mode == "Tx-Full":
                async with global_lock:
                    snapshot = dict(state.data)
                    await asyncio.sleep(random.uniform(0.0001, 0.001))
                    snapshot["key_A"] = snapshot.get("key_A", 0) + 1
                    state.data = snapshot
            elif mode == "Mutex-Per-Resource":
                # Both agents touch the shared JSON doc; lock the document
                async with per_resource.lock_for("shared_json"):
                    snapshot = dict(state.data)
                    await asyncio.sleep(random.uniform(0.0001, 0.001))
                    snapshot["key_A"] = snapshot.get("key_A", 0) + 1
                    state.data = snapshot
            elif mode == "OCC":
                ver = await occ.read_version("shared_json")
                snapshot = dict(state.data)
                await asyncio.sleep(random.uniform(0.0001, 0.001))
                snapshot["key_A"] = snapshot.get("key_A", 0) + 1
                if await occ.try_commit("shared_json", ver):
                    state.data = snapshot
                # else: lost update (optimistic abort)
            else:
                snapshot = dict(state.data)
                await asyncio.sleep(random.uniform(0.0001, 0.001))
                snapshot["key_A"] = snapshot.get("key_A", 0) + 1
                state.data = snapshot
            lat = (time.perf_counter_ns() - t0) / 1000.0
            latencies.append(lat)

    async def agent_b() -> None:
        for _ in range(ops_per_agent):
            t0 = time.perf_counter_ns()
            if mode == "Tx-Full":
                async with global_lock:
                    snapshot = dict(state.data)
                    await asyncio.sleep(random.uniform(0.0001, 0.001))
                    snapshot["key_B"] = snapshot.get("key_B", 0) + 1
                    state.data = snapshot
            elif mode == "Mutex-Per-Resource":
                async with per_resource.lock_for("shared_json"):
                    snapshot = dict(state.data)
                    await asyncio.sleep(random.uniform(0.0001, 0.001))
                    snapshot["key_B"] = snapshot.get("key_B", 0) + 1
                    state.data = snapshot
            elif mode == "OCC":
                ver = await occ.read_version("shared_json")
                snapshot = dict(state.data)
                await asyncio.sleep(random.uniform(0.0001, 0.001))
                snapshot["key_B"] = snapshot.get("key_B", 0) + 1
                if await occ.try_commit("shared_json", ver):
                    state.data = snapshot
                # else: lost update (optimistic abort)
            else:
                snapshot = dict(state.data)
                await asyncio.sleep(random.uniform(0.0001, 0.001))
                snapshot["key_B"] = snapshot.get("key_B", 0) + 1
                state.data = snapshot
            lat = (time.perf_counter_ns() - t0) / 1000.0
            latencies.append(lat)

    t_start = time.perf_counter()
    await asyncio.gather(agent_a(), agent_b())
    elapsed = time.perf_counter() - t_start

    correct = (
        state.data.get("key_A", 0) == ops_per_agent
        and state.data.get("key_B", 0) == ops_per_agent
    )
    if not correct:
        state.corruption_detected = True
    return RunMetrics(
        correct=correct,
        ops_count=total_ops,
        elapsed_s=elapsed,
        op_latencies_us=latencies,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

MODES = ["Tx-Full", "Mutex-Per-Resource", "OCC", "No-Frontier", "No-Tx"]
AGENT_COUNTS = [2, 4, 8]


class PerResourceLocks:
    """Per-resource mutex: only serialize agents touching the same resource."""

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}

    def lock_for(self, resource: str) -> asyncio.Lock:
        if resource not in self._locks:
            self._locks[resource] = asyncio.Lock()
        return self._locks[resource]

    async def acquire_all(self, resources: List[str]) -> None:
        for r in sorted(resources):  # sorted order prevents deadlock
            await self.lock_for(r).acquire()

    def release_all(self, resources: List[str]) -> None:
        for r in sorted(resources):
            lock = self._locks.get(r)
            if lock and lock.locked():
                lock.release()


class AsyncOCCVersionControl:
    """Async OCC: per-resource version counters with check-and-increment.

    read_version() returns current version. try_commit() atomically checks
    the version hasn't changed and increments it. No blocking on reads; only
    the commit can fail (optimistic).
    """

    def __init__(self):
        self._versions: Dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def read_version(self, resource: str) -> int:
        async with self._lock:
            return self._versions.get(resource, 0)

    async def try_commit(self, resource: str, read_version: int) -> bool:
        async with self._lock:
            current = self._versions.get(resource, 0)
            if current != read_version:
                return False
            self._versions[resource] = current + 1
            return True


async def run_full_benchmark(
    runs_per_config: int = 100, ops_per_agent: int = 50,
) -> Dict[str, Any]:
    """Run complete E3.7 benchmark suite."""
    results: Dict[str, Any] = {
        "disjoint_writes": {},
        "counter_scaling": {},
        "read_write_conflict": {},
        "structured_write_write": {},
        "metadata": {
            "runs_per_config": runs_per_config,
            "ops_per_agent": ops_per_agent,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    }

    total_configs = (
        len(AGENT_COUNTS) * len(MODES) * 2  # disjoint + counter
        + len(MODES) * 2  # read_write + structured
    )
    done = 0

    # --- Disjoint writes ---
    print("=== Disjoint Writes ===")
    for n_agents in AGENT_COUNTS:
        key = f"{n_agents}_agents"
        results["disjoint_writes"][key] = {}
        for mode in MODES:
            label = f"  {n_agents} agents, {mode}"
            print(f"{label}...", end=" ", flush=True)
            run_results = []
            for _ in range(runs_per_config):
                m = await run_disjoint_writes(mode, n_agents, ops_per_agent)
                run_results.append(m)
            agg = aggregate_metrics(run_results)
            results["disjoint_writes"][key][mode] = agg
            done += 1
            print(f"correct={agg['correctness_rate']*100:.0f}%  [{done}/{total_configs}]")

    # --- Counter scaling ---
    print("\n=== Counter Scaling ===")
    for n_agents in AGENT_COUNTS:
        key = f"{n_agents}_agents"
        results["counter_scaling"][key] = {}
        for mode in MODES:
            label = f"  {n_agents} agents, {mode}"
            print(f"{label}...", end=" ", flush=True)
            run_results = []
            for _ in range(runs_per_config):
                m = await run_counter_scaling(mode, n_agents, ops_per_agent)
                run_results.append(m)
            agg = aggregate_metrics(run_results)
            results["counter_scaling"][key][mode] = agg
            done += 1
            print(f"correct={agg['correctness_rate']*100:.0f}%  [{done}/{total_configs}]")

    # --- Read-write conflict ---
    print("\n=== Read-Write Conflict ===")
    results["read_write_conflict"]["2_agents"] = {}
    for mode in MODES:
        label = f"  2 agents, {mode}"
        print(f"{label}...", end=" ", flush=True)
        run_results = []
        for _ in range(runs_per_config):
            m = await run_read_write_conflict(mode, ops_per_agent)
            run_results.append(m)
        agg = aggregate_metrics(run_results)
        results["read_write_conflict"]["2_agents"][mode] = agg
        done += 1
        print(f"correct={agg['correctness_rate']*100:.0f}%  [{done}/{total_configs}]")

    # --- Structured write-write ---
    print("\n=== Structured Write-Write ===")
    results["structured_write_write"]["2_agents"] = {}
    for mode in MODES:
        label = f"  2 agents, {mode}"
        print(f"{label}...", end=" ", flush=True)
        run_results = []
        for _ in range(runs_per_config):
            m = await run_structured_write_write(mode, ops_per_agent)
            run_results.append(m)
        agg = aggregate_metrics(run_results)
        results["structured_write_write"]["2_agents"][mode] = agg
        done += 1
        print(f"correct={agg['correctness_rate']*100:.0f}%  [{done}/{total_configs}]")

    return results


def print_summary(results: Dict[str, Any]) -> None:
    """Print formatted summary tables."""
    print("\n" + "=" * 75)
    print("SUMMARY")
    print("=" * 75)

    for pattern in ["disjoint_writes", "counter_scaling", "read_write_conflict", "structured_write_write"]:
        data = results.get(pattern, {})
        if not data:
            continue
        print(f"\n--- {pattern.replace('_', ' ').title()} ---")
        print(f"{'Agents':<10} {'Mode':<15} {'Correct%':<10} {'Throughput(ops/s)':<20} {'p99 Lat(us)':<15}")
        print("-" * 70)
        for agents_key in sorted(data.keys()):
            for mode in MODES:
                agg = data[agents_key].get(mode)
                if not agg:
                    continue
                cr = agg["correctness_rate"] * 100
                tp = agg["throughput_ops_sec"]["mean"]
                p99 = agg["latency_us"]["p99"]["mean"]
                print(f"{agents_key:<10} {mode:<15} {cr:>6.1f}%   {tp:>14.0f}      {p99:>10.1f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="E3.7: Multi-Agent Contention Benchmark")
    parser.add_argument("--runs", type=int, default=100, help="Runs per configuration")
    parser.add_argument("--ops", type=int, default=50, help="Operations per agent per run")
    parser.add_argument("--output", type=str, help="Output JSON file")
    args = parser.parse_args()

    print("E3.7: Multi-Agent Contention Benchmark")
    print(f"Runs per config: {args.runs}, Ops per agent: {args.ops}")
    print(f"Total configs: {len(AGENT_COUNTS) * len(MODES) * 2 + len(MODES) * 2}")
    print(f"Total runs: {(len(AGENT_COUNTS) * len(MODES) * 2 + len(MODES) * 2) * args.runs}")
    print()

    start = time.time()
    results = asyncio.run(run_full_benchmark(
        runs_per_config=args.runs,
        ops_per_agent=args.ops,
    ))
    elapsed = time.time() - start

    results["metadata"]["duration_s"] = round(elapsed, 2)
    print_summary(results)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(results, indent=2))
        print(f"\nResults saved to {args.output}")

    print(f"\nCompleted in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
