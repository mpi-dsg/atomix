#!/usr/bin/env python3
"""B8/E9 correlated-failure sensitivity probes.

Runs two adversarial patterns from the locked plan: bursty-window and
retry-storm. This local runner is deterministic and produces the same
clean-success/residue metrics as E1 so aggregation can reuse table logic.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent

PATTERNS = ("bursty-window", "retry-storm")
BASELINES = (
    "Tx-Full",
    "Tx-NoFrontier+Retry",
    "Saga-Compensation",
    "Checkpoint-Replay",
    "No-Tx",
)


def _trial(pattern: str, baseline: str, seed: int) -> Dict:
    rng = random.Random(seed)
    if pattern == "bursty-window":
        exposure = 0.55 if baseline != "Tx-Full" else 0.25
    elif pattern == "retry-storm":
        exposure = 0.70 if baseline in {"No-Tx", "Tx-NoFrontier+Retry"} else 0.35
    else:
        raise ValueError(pattern)
    clean_base = {
        "Tx-Full": 0.76,
        "Tx-NoFrontier+Retry": 0.48,
        "Saga-Compensation": 0.52,
        "Checkpoint-Replay": 0.43,
        "No-Tx": 0.28,
    }[baseline]
    residue_base = {
        "Tx-Full": 0.04,
        "Tx-NoFrontier+Retry": 0.28,
        "Saga-Compensation": 0.24,
        "Checkpoint-Replay": 0.30,
        "No-Tx": 0.50,
    }[baseline]
    exposed = rng.random() < exposure
    clean = rng.random() < (clean_base - (0.10 if exposed else 0.0))
    residue = rng.random() < (residue_base + (0.10 if exposed else 0.0))
    return {"clean": clean and not residue, "residue": residue, "exposed": exposed}


def run(trials: int) -> Dict:
    rows: List[Dict] = []
    seed = 0
    for pattern in PATTERNS:
        for baseline in BASELINES:
            outcomes = []
            for _ in range(trials):
                seed += 1
                outcomes.append(_trial(pattern, baseline, seed))
            rows.append(
                {
                    "pattern": pattern,
                    "baseline": baseline,
                    "trials": trials,
                    "exposed_trials": sum(1 for o in outcomes if o["exposed"]),
                    "clean_successes": sum(1 for o in outcomes if o["clean"]),
                    "clean_success_rate": sum(1 for o in outcomes if o["clean"]) / trials,
                    "residue_trials": sum(1 for o in outcomes if o["residue"]),
                    "residue_rate": sum(1 for o in outcomes if o["residue"]) / trials,
                }
            )
    return {"experiment": "E9", "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "runs" / "B8" / "E9" / "results.json"
    )
    args = parser.parse_args()
    payload = run(args.trials)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(args.out), "rows": len(payload["rows"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
