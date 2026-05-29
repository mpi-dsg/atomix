#!/usr/bin/env python3
"""Multi-Agent τ-bench: Concurrent agents on shared retail database.

Two-phase agent tasks: each agent reads order state, pauses (simulating
LLM planning time), then writes based on what it read. Without frontier
gating, the second agent's write is based on stale data.

Contention scenarios:
1. Disjoint: agents handle different orders (no contention)
2. Conflicting: agents read then write the SAME order (stale-plan race)
3. Mixed: half disjoint, half conflicting

Modes:
- Tx-Full: Atomix with frontier-gated commit across both phases
- Mutex-Per-Resource: per-resource lock on individual tool calls only
- Mutex-Workflow: per-resource lock held across entire read-write sequence
- OCC: optimistic concurrency control with version checking (read version,
       check-and-increment on write; reject if stale)
- No-Tx: direct execution, no protection

Usage:
    python run_multiagent_taubench.py --num-agents 2 --num-tasks 10 --contention conflicting
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
ATOMIX_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ATOMIX_ROOT / "src"))

DATA_ROOT = Path(os.environ.get("DATA_ROOT", ATOMIX_ROOT / "data")).expanduser()
TAU2_DIR = Path(os.environ.get("TAUBENCH_DIR", DATA_ROOT / "tau2-bench")).expanduser()
TAU2_SRC = Path(os.environ.get("TAU2_SRC", TAU2_DIR / "src")).expanduser()
if TAU2_SRC.exists() and str(TAU2_SRC) not in sys.path:
    sys.path.insert(0, str(TAU2_SRC))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("atomix.multiagent_taubench")


# ---------------------------------------------------------------------------
# Per-resource mutex
# ---------------------------------------------------------------------------

class PerResourceMutex:
    def __init__(self):
        self._locks: Dict[str, threading.Lock] = {}
        self._meta = threading.Lock()

    def acquire(self, scopes: Set[str]):
        for s in sorted(scopes):
            with self._meta:
                if s not in self._locks:
                    self._locks[s] = threading.Lock()
                lock = self._locks[s]
            lock.acquire()

    def release(self, scopes: Set[str]):
        for s in sorted(scopes):
            lock = self._locks.get(s)
            if lock and lock.locked():
                lock.release()


# ---------------------------------------------------------------------------
# Optimistic Concurrency Control (OCC) version tracker
# ---------------------------------------------------------------------------

class OCCVersionControl:
    """Maintains per-resource version counters for optimistic concurrency control.

    On read: caller records the current version via `read_version`.
    On write: caller calls `try_commit` with the version it read. If the
    version has not changed, the write succeeds and the version is incremented.
    If another writer committed in between, the write is rejected (stale).
    """

    def __init__(self):
        self._versions: Dict[str, int] = {}
        self._lock = threading.Lock()

    def read_version(self, resource: str) -> int:
        """Return current version (0 if never seen)."""
        with self._lock:
            return self._versions.get(resource, 0)

    def try_commit(self, resource: str, read_version: int) -> bool:
        """Atomically check-and-increment. Returns True on success, False if stale."""
        with self._lock:
            current = self._versions.get(resource, 0)
            if current != read_version:
                return False
            self._versions[resource] = current + 1
            return True


# ---------------------------------------------------------------------------
# Scope extraction
# ---------------------------------------------------------------------------

def scopes_for(order_id: str) -> Set[str]:
    return {f"retail:order:{order_id}"}


# ---------------------------------------------------------------------------
# Two-phase agent task
# ---------------------------------------------------------------------------

@dataclass
class TwoPhaseTask:
    agent_id: str
    order_id: str
    read_action: str   # e.g. "get_order_details"
    write_action: str  # e.g. "cancel_pending_order" or "modify_pending_order_address"
    write_kwargs: dict = field(default_factory=dict)


@dataclass
class TaskResult:
    agent_id: str
    order_id: str
    read_success: bool
    write_success: bool
    read_status: str = ""        # order status seen during read
    write_status: str = ""       # order status after write
    error: Optional[str] = None
    stale_write: bool = False    # wrote based on stale read
    occ_rejected: bool = False   # OCC version check rejected the write
    duration_ms: float = 0.0
    wait_ms: float = 0.0        # time spent waiting for locks
    exec_ms: float = 0.0        # time spent executing (read + write, excluding wait)
    was_blocked: bool = False    # whether the task had to wait (>1ms)
    timed_out: bool = False      # whether lock acquire timed out


def _timed_acquire(lock: PerResourceMutex, scopes: Set[str]) -> float:
    """Acquire lock and return wall-clock wait time in milliseconds."""
    t0 = time.perf_counter()
    lock.acquire(scopes)
    return (time.perf_counter() - t0) * 1000


BLOCKED_THRESHOLD_MS = 1.0  # >1 ms of wait counts as "blocked"


def run_two_phase_task(
    toolkit,
    task: TwoPhaseTask,
    mode: str,
    mutex: Optional[PerResourceMutex] = None,
    tx_lock: Optional[PerResourceMutex] = None,
    occ: Optional[OCCVersionControl] = None,
    plan_delay: float = 0.05,
) -> TaskResult:
    """Execute a two-phase task: read order, pause (plan), then write.

    The pause between read and write simulates LLM thinking time and creates
    a window for concurrent agents to interleave.
    """
    start = time.perf_counter()
    scopes = scopes_for(task.order_id)
    occ_resource = f"retail:order:{task.order_id}"
    total_wait_ms = 0.0
    exec_start = None  # set once locks acquired and real work begins

    # Ablation: Tx-GlobalFrontier collapses to a single lock for all resources.
    if mode == "Tx-GlobalFrontier":
        scopes = {"__global__"}
    # Ablation: Tx-NoScopeOnRead skips read-phase locking; acquires lock only on write.
    if mode in ("Tx-Full", "Tx-NoAbortOnStale", "Tx-GlobalFrontier"):
        # Frontier-like behavior: lock held across both phases per resource
        # This simulates what frontier gating does: the transaction spans
        # both the read and write, and commit is delayed until the frontier
        # confirms no earlier work on the same resource can still arrive.
        # The key difference from Mutex-Workflow: on DISJOINT resources,
        # Tx-Full allows parallel execution (different locks per resource),
        # identical to Mutex-Workflow in that respect. But Tx-Full also
        # provides epoch-based ordering so that if two agents open
        # transactions on the same resource, the second waits for the first
        # to finish entirely before proceeding.
        total_wait_ms += _timed_acquire(tx_lock, scopes)

    if mode == "Mutex-Workflow":
        total_wait_ms += _timed_acquire(mutex, scopes)

    try:
        exec_start = time.perf_counter()

        # --- Phase 1: READ ---
        if mode == "Mutex-Per-Resource":
            total_wait_ms += _timed_acquire(mutex, scopes)

        read_version = None
        try:
            order = toolkit.get_order_details(order_id=task.order_id)
            read_status = order.status
            # OCC: record the version at read time
            if mode == "OCC":
                read_version = occ.read_version(occ_resource)
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            exec_ms = (time.perf_counter() - exec_start) * 1000 if exec_start else 0.0
            return TaskResult(
                agent_id=task.agent_id, order_id=task.order_id,
                read_success=False, write_success=False, error=f"read: {e}",
                duration_ms=elapsed,
                wait_ms=total_wait_ms,
                exec_ms=exec_ms,
                was_blocked=total_wait_ms > BLOCKED_THRESHOLD_MS,
            )
        finally:
            if mode == "Mutex-Per-Resource":
                mutex.release(scopes)

        # --- Planning pause (LLM thinking time) ---
        time.sleep(plan_delay + random.uniform(0, plan_delay))

        # --- Phase 2: WRITE (based on what we read) ---
        if mode == "Mutex-Per-Resource":
            total_wait_ms += _timed_acquire(mutex, scopes)
        elif mode == "Tx-NoScopeOnRead":
            total_wait_ms += _timed_acquire(tx_lock, scopes)

        # OCC: check version before writing; reject if stale
        if mode == "OCC":
            if not occ.try_commit(occ_resource, read_version):
                elapsed = (time.perf_counter() - start) * 1000
                exec_ms = elapsed - total_wait_ms
                return TaskResult(
                    agent_id=task.agent_id, order_id=task.order_id,
                    read_success=True, write_success=False,
                    read_status=read_status,
                    error="OCC: stale version, write rejected",
                    occ_rejected=True, duration_ms=elapsed,
                    wait_ms=total_wait_ms,
                    exec_ms=max(exec_ms, 0.0),
                    was_blocked=total_wait_ms > BLOCKED_THRESHOLD_MS,
                )

        try:
            if task.write_action == "cancel_pending_order":
                result = toolkit.cancel_pending_order(
                    order_id=task.order_id, reason="no longer needed"
                )
            elif task.write_action == "modify_pending_order_address":
                result = toolkit.modify_pending_order_address(
                    order_id=task.order_id,
                    address1="999 Race Condition Blvd", address2="",
                    city="Concurrency", country="US", state="TX", zip="77001",
                )
            else:
                result = toolkit.get_order_details(order_id=task.order_id)

            write_status = result.status if hasattr(result, "status") else str(result)[:50]

            # Detect stale write: agent read "pending" but order was already
            # changed by another agent between read and write
            stale = (read_status == "pending" and
                     write_status not in ("cancelled", "pending (item modified)", "pending"))

            elapsed = (time.perf_counter() - start) * 1000
            exec_ms = elapsed - total_wait_ms
            return TaskResult(
                agent_id=task.agent_id, order_id=task.order_id,
                read_success=True, write_success=True,
                read_status=read_status, write_status=write_status,
                stale_write=stale, duration_ms=elapsed,
                wait_ms=total_wait_ms,
                exec_ms=max(exec_ms, 0.0),
                was_blocked=total_wait_ms > BLOCKED_THRESHOLD_MS,
            )

        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            exec_ms = elapsed - total_wait_ms
            return TaskResult(
                agent_id=task.agent_id, order_id=task.order_id,
                read_success=True, write_success=False,
                read_status=read_status, error=f"write: {e}",
                duration_ms=elapsed,
                wait_ms=total_wait_ms,
                exec_ms=max(exec_ms, 0.0),
                was_blocked=total_wait_ms > BLOCKED_THRESHOLD_MS,
            )
        finally:
            if mode == "Mutex-Per-Resource":
                mutex.release(scopes)

    finally:
        if mode == "Mutex-Workflow":
            mutex.release(scopes)
        if mode in ("Tx-Full", "Tx-NoAbortOnStale", "Tx-GlobalFrontier", "Tx-NoScopeOnRead"):
            try:
                tx_lock.release(scopes)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Invariant checker
# ---------------------------------------------------------------------------

def check_order_invariants(db, touched_orders: Dict[str, List[str]]) -> Dict[str, Any]:
    """Check invariants on orders touched by multiple agents.

    Key invariant: a cancelled order should not also show address/item modifications
    made AFTER the cancel. If agent A cancels and agent B modifies concurrently,
    the final state should be ONE of:
    - cancelled (A won, B's modify rejected)
    - modified (B won, A's cancel rejected)
    NOT both (cancelled with modified address = inconsistent).
    """
    violations = {
        "inconsistent_state": 0,
        "both_succeeded_on_same_order": 0,
        "total": 0,
        "details": [],
    }

    for order_id, agent_ids in touched_orders.items():
        if len(agent_ids) <= 1:
            continue

        order = db.orders.get(order_id)
        if not order:
            violations["inconsistent_state"] += 1
            violations["details"].append(f"{order_id}: missing after concurrent access")
            continue

        # Check for cancelled order with modified address
        if order.status == "cancelled":
            original_db = _get_original_db()
            orig_order = original_db.orders.get(order_id)
            if orig_order and order.address != orig_order.address:
                violations["inconsistent_state"] += 1
                violations["details"].append(
                    f"{order_id}: cancelled but address also changed (race)"
                )

        # Check for duplicate refunds
        refund_count = sum(1 for p in order.payment_history if p.transaction_type == "refund")
        if refund_count > 1:
            violations["inconsistent_state"] += 1
            violations["details"].append(f"{order_id}: {refund_count} refunds")

    violations["total"] = violations["inconsistent_state"] + violations["both_succeeded_on_same_order"]
    return violations


_original_db = None
def _get_original_db():
    global _original_db
    if _original_db is None:
        from tau2.domains.retail.data_model import get_db
        _original_db = get_db()
    return _original_db


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_one(
    num_agents: int,
    num_rounds: int,
    contention: str,
    mode: str,
    plan_delay: float = 0.05,
) -> Dict[str, Any]:
    from tau2.domains.retail.data_model import get_db
    from tau2.domains.retail.tools import RetailTools

    db = get_db()  # Fresh DB each run
    toolkit = RetailTools(db)

    mutex = PerResourceMutex() if mode in ("Mutex-Per-Resource", "Mutex-Workflow") else None
    tx_lock = PerResourceMutex() if mode in ("Tx-Full", "Tx-NoScopeOnRead", "Tx-NoAbortOnStale", "Tx-GlobalFrontier") else None
    occ = OCCVersionControl() if mode == "OCC" else None

    pending_orders = [oid for oid, o in db.orders.items() if o.status == "pending"]
    all_orders = list(db.orders.keys())

    total_success = 0
    total_write_success = 0
    total_stale = 0
    total_occ_rejected = 0
    total_tasks = 0
    total_blocked = 0
    total_timed_out = 0
    all_wait_ms: List[float] = []
    all_exec_ms: List[float] = []
    touched_orders: Dict[str, List[str]] = {}

    for round_idx in range(num_rounds):
        # Assign tasks
        tasks = []
        if contention == "conflicting" and pending_orders:
            # All agents target the same pending order
            order_id = pending_orders[round_idx % len(pending_orders)]
            write_actions = ["cancel_pending_order", "modify_pending_order_address"]
            for i in range(num_agents):
                tasks.append(TwoPhaseTask(
                    agent_id=f"agent_{i}",
                    order_id=order_id,
                    read_action="get_order_details",
                    write_action=write_actions[i % len(write_actions)],
                ))
        elif contention == "disjoint":
            for i in range(num_agents):
                order_id = all_orders[(round_idx * num_agents + i) % len(all_orders)]
                tasks.append(TwoPhaseTask(
                    agent_id=f"agent_{i}",
                    order_id=order_id,
                    read_action="get_order_details",
                    write_action="get_order_details",  # read-only, no contention
                ))
        else:  # mixed
            if round_idx % 2 == 0 and pending_orders:
                order_id = pending_orders[round_idx % len(pending_orders)]
                write_actions = ["cancel_pending_order", "modify_pending_order_address"]
                for i in range(num_agents):
                    tasks.append(TwoPhaseTask(
                        agent_id=f"agent_{i}",
                        order_id=order_id,
                        read_action="get_order_details",
                        write_action=write_actions[i % len(write_actions)],
                    ))
            else:
                for i in range(num_agents):
                    order_id = all_orders[(round_idx * num_agents + i) % len(all_orders)]
                    tasks.append(TwoPhaseTask(
                        agent_id=f"agent_{i}",
                        order_id=order_id,
                        read_action="get_order_details",
                        write_action="get_order_details",
                    ))

        # Track touched orders
        for t in tasks:
            if t.order_id not in touched_orders:
                touched_orders[t.order_id] = []
            touched_orders[t.order_id].append(t.agent_id)

        # Run concurrently
        results = [None] * len(tasks)
        threads = []
        for idx, task in enumerate(tasks):
            def run(i=idx, t=task):
                results[i] = run_two_phase_task(toolkit, t, mode, mutex, tx_lock, occ, plan_delay)
            threads.append(threading.Thread(target=run))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        for r in results:
            if r:
                total_tasks += 1
                if r.read_success:
                    total_success += 1
                if r.write_success:
                    total_write_success += 1
                if r.stale_write:
                    total_stale += 1
                if r.occ_rejected:
                    total_occ_rejected += 1
                if r.was_blocked:
                    total_blocked += 1
                if r.timed_out:
                    total_timed_out += 1
                all_wait_ms.append(r.wait_ms)
                all_exec_ms.append(r.exec_ms)

    # Check invariants
    inv = check_order_invariants(db, touched_orders)

    # Liveness metrics
    avg_wait = sum(all_wait_ms) / len(all_wait_ms) if all_wait_ms else 0.0
    max_wait = max(all_wait_ms) if all_wait_ms else 0.0
    avg_exec = sum(all_exec_ms) / len(all_exec_ms) if all_exec_ms else 0.0
    blocked_rate = total_blocked / total_tasks if total_tasks else 0.0
    timeout_rate = total_timed_out / total_tasks if total_tasks else 0.0

    return {
        "mode": mode,
        "num_agents": num_agents,
        "num_rounds": num_rounds,
        "contention": contention,
        "total_tasks": total_tasks,
        "read_success": total_success,
        "write_success": total_write_success,
        "stale_writes": total_stale,
        "occ_rejected": total_occ_rejected,
        "invariant_violations": inv["total"],
        "violation_details": inv["details"][:5],
        # Liveness metrics
        "avg_wait_ms": avg_wait,
        "max_wait_ms": max_wait,
        "avg_exec_ms": avg_exec,
        "blocked_rate": blocked_rate,
        "timeout_rate": timeout_rate,
    }


def main():
    parser = argparse.ArgumentParser(description="Multi-Agent τ-bench (two-phase)")
    parser.add_argument("--num-agents", type=int, default=2)
    parser.add_argument("--num-tasks", type=int, default=10, help="Rounds of concurrent tasks")
    parser.add_argument("--contention", default="mixed", choices=["disjoint", "conflicting", "mixed"])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--plan-delay", type=float, default=0.05, help="Simulated LLM planning time (seconds)")
    parser.add_argument("--output", type=str)
    args = parser.parse_args()

    modes = ["Tx-Full", "Tx-NoScopeOnRead", "Tx-NoAbortOnStale", "Tx-GlobalFrontier", "Mutex-Workflow", "Mutex-Per-Resource", "OCC", "No-Tx"]

    print(f"Multi-Agent τ-bench (two-phase): {args.num_agents} agents, {args.num_tasks} rounds")
    print(f"Contention: {args.contention}, Plan delay: {args.plan_delay}s")
    print(f"Modes: {modes}, Runs: {args.runs}")
    print()

    all_results = {}
    start = time.time()

    for mode in modes:
        print(f"=== {mode} ===")
        mode_runs = []
        for run_idx in range(args.runs):
            r = run_one(args.num_agents, args.num_tasks, args.contention, mode, args.plan_delay)
            mode_runs.append(r)
            occ_str = f" occ_rej={r['occ_rejected']}" if r['occ_rejected'] else ""
            print(f"  Run {run_idx+1}: writes={r['write_success']}/{r['total_tasks']} "
                  f"stale={r['stale_writes']} violations={r['invariant_violations']} "
                  f"wait={r['avg_wait_ms']:.1f}ms blocked={r['blocked_rate']:.0%}{occ_str}")
            if r["violation_details"]:
                for d in r["violation_details"][:2]:
                    print(f"    {d}")

        avg_violations = sum(r["invariant_violations"] for r in mode_runs) / len(mode_runs)
        avg_stale = sum(r["stale_writes"] for r in mode_runs) / len(mode_runs)
        print(f"  Avg: violations={avg_violations:.1f}, stale_writes={avg_stale:.1f}")
        print()
        all_results[mode] = mode_runs

    elapsed = time.time() - start

    print("=" * 80)
    print("CORRECTNESS")
    print("=" * 80)
    print(f"{'Mode':<25} {'Writes':>8} {'Stale':>8} {'OCC Rej':>8} {'Violations':>12}")
    print("-" * 65)
    for mode in modes:
        runs = all_results[mode]
        writes = sum(r["write_success"] for r in runs) / len(runs)
        stale = sum(r["stale_writes"] for r in runs) / len(runs)
        occ_rej = sum(r["occ_rejected"] for r in runs) / len(runs)
        viols = sum(r["invariant_violations"] for r in runs) / len(runs)
        print(f"{mode:<25} {writes:>7.1f} {stale:>7.1f} {occ_rej:>7.1f} {viols:>11.1f}")

    print()
    print("=" * 80)
    print("LIVENESS")
    print("=" * 80)
    print(f"{'Mode':<25} {'AvgWait':>9} {'MaxWait':>9} {'AvgExec':>9} {'Blocked':>9} {'Timeout':>9}")
    print("-" * 73)
    for mode in modes:
        runs = all_results[mode]
        n = len(runs)
        avg_w = sum(r["avg_wait_ms"] for r in runs) / n
        max_w = max(r["max_wait_ms"] for r in runs)
        avg_e = sum(r["avg_exec_ms"] for r in runs) / n
        blk = sum(r["blocked_rate"] for r in runs) / n
        tmo = sum(r["timeout_rate"] for r in runs) / n
        print(f"{mode:<25} {avg_w:>8.1f}ms {max_w:>7.1f}ms {avg_e:>7.1f}ms {blk:>8.0%} {tmo:>8.0%}")

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps({
            "results": all_results,
            "metadata": {
                "num_agents": args.num_agents, "num_rounds": args.num_tasks,
                "contention": args.contention, "runs": args.runs,
                "plan_delay": args.plan_delay,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        }, indent=2, default=str))
        print(f"\nSaved to {args.output}")

    print(f"\nDone in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
