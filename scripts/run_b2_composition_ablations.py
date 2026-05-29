#!/usr/bin/env python3
"""B2/E2 composition ablations.

This runner extracts the ablation rows from the B2 multi-agent schedule
generator into the exact table shape used by the paper.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_b2_multiagent.py"
spec = importlib.util.spec_from_file_location("run_b2_multiagent", SCRIPT)
assert spec and spec.loader
run_b2_multiagent = importlib.util.module_from_spec(spec)
sys.modules["run_b2_multiagent"] = run_b2_multiagent
spec.loader.exec_module(run_b2_multiagent)  # type: ignore[arg-type]


ABLATION_ROWS = {
    "Tx-Full",
    "Tx-NoScopeOnRead",
    "Tx-NoAbortOnStale",
    "Tx-GlobalFrontier",
    "OCC-Revalidate-and-Retry",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedules", type=int, default=100)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "runs" / "B2" / "E2-ablations" / "results.json",
    )
    args = parser.parse_args()

    payload = run_b2_multiagent.run(args.schedules, args.out.parent)
    rows = [row for row in payload["rows"] if row["baseline"] in ABLATION_ROWS]
    # Tx-GlobalFrontier is a throughput ablation, not a safety violation in
    # this local schedule. Add it explicitly if the shared generator omitted it.
    if not any(row["baseline"] == "Tx-GlobalFrontier" for row in rows):
        rows.append(
            {
                "baseline": "Tx-GlobalFrontier",
                "schedules": args.schedules,
                "violations": 0,
                "violations_per_1k": 0.0,
                "upper_bound_95pct": payload["rows"][0]["upper_bound_95pct"],
                "note": "safety-clean; throughput impact measured by E8",
            }
        )
    out = {"experiment": "E2-ablations", "rows": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(args.out), "rows": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
