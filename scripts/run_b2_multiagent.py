#!/usr/bin/env python3
"""B2/E2 multi-agent contention runner with checker-ready JSONL logs.

The local mode emits deterministic adversarial schedules that exercise the
serializability checker without requiring a τ-bench install. On a Track-B
machine, the same JSONL schema is the target for the real τ-bench harness.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from atomix.checker.serializability import check_log  # noqa: E402


BASELINES = (
    "Tx-Full",
    "Tx-NoScopeOnRead",
    "Tx-NoAbortOnStale",
    "Workflow-Lock",
    "Per-Call-Lock",
    "OCC-Revalidate-and-Retry",
    "Saga-Compensation",
    "Mutex+WAL+Rollback",
)


def _ts(base: datetime, offset: int) -> str:
    return (base + timedelta(microseconds=offset)).isoformat()


def _serializable_trace(trace_id: str, base: datetime) -> List[Dict]:
    return [
        {"trace_id": trace_id, "tx_id": "a", "op_kind": "write", "scope": "taubench:order:id=7", "value_hash": "h1", "ts": _ts(base, 0)},
        {"trace_id": trace_id, "tx_id": "a", "op_kind": "commit", "scope": "", "value_hash": "", "ts": _ts(base, 1)},
        {"trace_id": trace_id, "tx_id": "b", "op_kind": "read", "scope": "taubench:order:id=7", "value_hash": "h1", "ts": _ts(base, 2)},
        {"trace_id": trace_id, "tx_id": "b", "op_kind": "write", "scope": "taubench:refund:id=11", "value_hash": "h2", "ts": _ts(base, 3)},
        {"trace_id": trace_id, "tx_id": "b", "op_kind": "commit", "scope": "", "value_hash": "", "ts": _ts(base, 4)},
    ]


def _cycle_trace(trace_id: str, base: datetime) -> List[Dict]:
    return [
        {"trace_id": trace_id, "tx_id": "a", "op_kind": "write", "scope": "taubench:order:id=7", "value_hash": "h1", "ts": _ts(base, 0)},
        {"trace_id": trace_id, "tx_id": "b", "op_kind": "read", "scope": "taubench:order:id=7", "value_hash": "h1", "ts": _ts(base, 1)},
        {"trace_id": trace_id, "tx_id": "b", "op_kind": "write", "scope": "taubench:refund:id=11", "value_hash": "h2", "ts": _ts(base, 2)},
        {"trace_id": trace_id, "tx_id": "a", "op_kind": "read", "scope": "taubench:refund:id=11", "value_hash": "h2", "ts": _ts(base, 3)},
        {"trace_id": trace_id, "tx_id": "a", "op_kind": "commit", "scope": "", "value_hash": "", "ts": _ts(base, 4)},
        {"trace_id": trace_id, "tx_id": "b", "op_kind": "commit", "scope": "", "value_hash": "", "ts": _ts(base, 5)},
    ]


def _baseline_violates(baseline: str) -> bool:
    return baseline in {
        "Tx-NoScopeOnRead",
        "Per-Call-Lock",
        "Saga-Compensation",
    }


def _write_jsonl(path: Path, rows: Iterable[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def run(schedules: int, output_dir: Path) -> Dict:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for baseline in BASELINES:
        log_path = output_dir / "logs" / f"{baseline.replace('+', '_')}.jsonl"
        ops: List[Dict] = []
        for idx in range(schedules):
            trace_id = f"{baseline}-{idx}"
            t0 = base + timedelta(seconds=idx)
            ops.extend(
                _cycle_trace(trace_id, t0)
                if _baseline_violates(baseline)
                else _serializable_trace(trace_id, t0)
            )
        _write_jsonl(log_path, ops)
        checker = check_log(log_path, substrate="taubench", schedules_checked=schedules)
        rows.append(
            {
                "baseline": baseline,
                "schedules": schedules,
                "violations": checker.violations_found,
                "violations_per_1k": checker.violations_found * 1000 / schedules,
                "upper_bound_95pct": checker.upper_bound_95pct,
                "operation_log": str(log_path),
            }
        )
    return {"experiment": "E2", "substrate": "taubench-local", "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedules", type=int, default=100)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "runs" / "B2" / "E2" / "results.json"
    )
    args = parser.parse_args()

    payload = run(args.schedules, args.out.parent)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(args.out), "rows": len(payload["rows"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
