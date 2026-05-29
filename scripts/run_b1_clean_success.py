#!/usr/bin/env python3
"""B1/E1 clean-success driver with final-state-oracle outputs.

By default this is a deterministic local smoke runner. Pass ``--real`` only on
a prepared Track-B machine; the script then records the matrix as
environment-gated rather than fabricating real LLM results.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from atomix.oracles import clean_success  # noqa: E402
from atomix.oracles.osworld import FsSnapshot, OSWorldEvaluationContext  # noqa: E402
from atomix.oracles.taubench import TauBenchEvaluationContext  # noqa: E402
from atomix.oracles.webarena import WebArenaEvaluationContext  # noqa: E402


WORKLOADS = ("webarena", "osworld", "taubench")
BASELINES = (
    "Tx-Full",
    "Saga-Compensation",
    "Checkpoint-Replay",
    "OCC-Revalidate-and-Retry",
    "No-Tx",
)
FAULT_CLASSES = ("F1", "F2", "F3", "F4", "F5")


def _cp_zero_upper(n: int, alpha: float = 0.05) -> float:
    if n <= 0:
        return 1.0
    return 1.0 - (alpha / 2.0) ** (1.0 / n)


def _mock_context(workload: str, success: bool, residue: bool):
    if workload == "webarena":
        dom_before = {"shop": {"#cart": "0"}}
        dom_after = {"shop": {"#cart": "1" if residue else "0"}}
        return WebArenaEvaluationContext(
            evaluation_fn=lambda: {"score": 1.0 if success else 0.0},
            dom_before=dom_before,
            dom_after=dom_after,
        )
    if workload == "osworld":
        before = FsSnapshot()
        after = FsSnapshot({"tmp/task.txt": (4, 1, "hash")}) if residue else before
        return OSWorldEvaluationContext(
            evaluation_fn=lambda: success,
            fs_before=before,
            fs_after=after,
            proc_before=set(),
            proc_after=set(),
        )
    before_db = {"orders": {}, "customers": {}, "refunds": {}}
    after_db = (
        {"orders": {"o1": {"status": "created"}}, "customers": {}, "refunds": {}}
        if residue
        else before_db
    )
    return TauBenchEvaluationContext(
        evaluation_fn=lambda: {"success": success},
        db_before=before_db,
        db_after=after_db,
    )


def _mock_trial(workload: str, baseline: str, fault: str, seed: int) -> Dict[str, Any]:
    rng = random.Random(seed)
    # F2 is the ambiguous post-effect/pre-return class where transactional
    # effect routing is expected to separate from retry-only baselines.
    base_success = {
        "Tx-Full": 0.78,
        "OCC-Revalidate-and-Retry": 0.58,
        "Saga-Compensation": 0.52,
        "Checkpoint-Replay": 0.46,
        "No-Tx": 0.30,
    }[baseline]
    if fault == "F2":
        residue_prob = {
            "Tx-Full": 0.02,
            "OCC-Revalidate-and-Retry": 0.24,
            "Saga-Compensation": 0.30,
            "Checkpoint-Replay": 0.36,
            "No-Tx": 0.55,
        }[baseline]
    else:
        residue_prob = 0.02 if baseline == "Tx-Full" else 0.12
    success = rng.random() < base_success
    residue = rng.random() < residue_prob
    clean, residue_records = clean_success(
        workload,
        task_id=f"{workload}-{seed}",
        ctx=_mock_context(workload, success, residue),
    )
    return {
        "workload": workload,
        "baseline": baseline,
        "fault_class": fault,
        "goal_achieved": success,
        "clean_success": clean,
        "residue_count": len(residue_records),
    }


def run_mock(tasks_per_cell: int) -> Dict[str, Any]:
    trials: List[Dict[str, Any]] = []
    seed = 0
    for workload in WORKLOADS:
        for baseline in BASELINES:
            for fault in FAULT_CLASSES:
                for _ in range(tasks_per_cell):
                    seed += 1
                    trials.append(_mock_trial(workload, baseline, fault, seed))

    rows: List[Dict[str, Any]] = []
    for workload in WORKLOADS:
        for baseline in BASELINES:
            for fault in FAULT_CLASSES:
                cell = [
                    t
                    for t in trials
                    if t["workload"] == workload
                    and t["baseline"] == baseline
                    and t["fault_class"] == fault
                ]
                clean = sum(1 for t in cell if t["clean_success"])
                residue = sum(1 for t in cell if t["residue_count"] > 0)
                rows.append(
                    {
                        "workload": workload,
                        "baseline": baseline,
                        "fault_class": fault,
                        "tasks": len(cell),
                        "clean_successes": clean,
                        "clean_success_rate": clean / max(1, len(cell)),
                        "residue_trials": residue,
                        "residue_rate": residue / max(1, len(cell)),
                    }
                )
    return {
        "experiment": "E1",
        "mode": "mock",
        "rows": rows,
        "upper_bound_95pct_if_zero": _cp_zero_upper(tasks_per_cell),
    }


def run_real_manifest(tasks_per_workload: int) -> Dict[str, Any]:
    return {
        "experiment": "E1",
        "mode": "real-manifest",
        "status": "requires Track-B environment and API keys",
        "workloads": list(WORKLOADS),
        "baselines": list(BASELINES),
        "fault_classes": list(FAULT_CLASSES),
        "tasks_per_workload": tasks_per_workload,
        "required_scripts": [
            "scripts/run_workload_experiment.py",
            "scripts/check_track_b_env.sh",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-per-cell", type=int, default=30)
    parser.add_argument("--real", action="store_true")
    parser.add_argument(
        "--out", type=Path, default=ROOT / "runs" / "B1" / "E1" / "results.json"
    )
    args = parser.parse_args()

    payload = (
        run_real_manifest(args.tasks_per_cell)
        if args.real
        else run_mock(args.tasks_per_cell)
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(args.out), "mode": payload["mode"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
