#!/usr/bin/env python3
"""B6 speculative-WebArena verification using the local mock harness."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_webarena_atomix.py"
spec = importlib.util.spec_from_file_location("run_webarena_atomix", SCRIPT)
assert spec and spec.loader
run_webarena_atomix = importlib.util.module_from_spec(spec)
sys.modules["run_webarena_atomix"] = run_webarena_atomix
spec.loader.exec_module(run_webarena_atomix)  # type: ignore[arg-type]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=10)
    parser.add_argument("--k", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument(
        "--out", type=Path, default=ROOT / "runs" / "B6" / "webarena-spec" / "results.json"
    )
    args = parser.parse_args()
    rows = []
    result_dir = args.out.parent / "raw"
    for k in args.k:
        summary = run_webarena_atomix.run_webarena_mock(
            mode="Tx-Full",
            fault_probability=0.02,
            num_tasks=args.tasks,
            steps_per_task=8,
            result_dir=result_dir,
            speculative_k=k,
        )
        rows.append(
            {
                "k": k,
                "tasks": args.tasks,
                "success_rate": summary.get("success_rate", 0.0),
                "faults": summary.get("total_faults", 0),
            }
        )
    payload = {"experiment": "B6-spec-webarena", "rows": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(args.out), "rows": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
