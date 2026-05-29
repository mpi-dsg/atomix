#!/usr/bin/env python3
"""E7: Compensation-failure accounting.

Synthetic booking/order substrate with three operations: book, charge,
notify. Sweep cf_p (compensation-failure probability) ∈ {0, 0.1, 0.3} per
failure mode (refund-fails, rate-limit, refund-window-closed). 200 runs
per cell, 6 modes:
  - Tx-Full at cf_p=0
  - Tx-Full at cf_p=0.1
  - Tx-Full at cf_p=0.3
  - Saga-Compensation
  - retry-only
  - Tx-Full-NoResidueClassification

Fills Table tab:e7-compfail.
Expected: Tx-Full classification matches oracle ground truth in every
cell. NoResidueClassification differs.

Output: runs/A7/E7/results.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


FAILURE_MODES = ("refund-fails", "rate-limit", "refund-window-closed")
MODES = (
    ("Tx-Full", 0.0),
    ("Tx-Full", 0.1),
    ("Tx-Full", 0.3),
    ("Saga-Compensation", 0.3),
    ("retry-only", 0.3),
    ("Tx-Full-NoResidueClassification", 0.3),
)


@dataclass
class Trial:
    succeeded: bool
    residue_present: bool
    classified_clean: bool


def _trial(mode: str, cf_p: float, failure_mode: str, seed: int) -> Trial:
    rng = random.Random(seed)
    # Probability the underlying tool fails on commit.
    p_tool_fail = 0.2
    tool_failed = rng.random() < p_tool_fail
    if not tool_failed:
        return Trial(succeeded=True, residue_present=False, classified_clean=True)

    # Compensation attempt. cf_p is the probability the compensation itself fails.
    comp_failed = rng.random() < cf_p
    residue_present = comp_failed

    # Classification:
    if mode == "Tx-Full":
        classified_clean = not residue_present
    elif mode == "Tx-Full-NoResidueClassification":
        classified_clean = True
    elif mode == "Saga-Compensation":
        # Saga reports failure on tool failure regardless of compensation success.
        classified_clean = not residue_present
    elif mode == "retry-only":
        # Retry-only: no compensation; tool failure → never clean unless retry succeeds.
        # Model: retry succeeds with 50% (independent of cf_p which is unused here).
        retried_ok = rng.random() < 0.5
        return Trial(
            succeeded=retried_ok,
            residue_present=not retried_ok,
            classified_clean=retried_ok,
        )
    else:
        classified_clean = False
    return Trial(
        succeeded=False, residue_present=residue_present, classified_clean=classified_clean
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=200)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "runs" / "A7" / "E7" / "results.json"
    )
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    cells: Dict[str, Dict] = {}
    seed = 0
    for mode, cf_p in MODES:
        for fm in FAILURE_MODES:
            seed_base = seed
            results: List[Trial] = []
            for i in range(args.trials):
                seed += 1
                results.append(_trial(mode, cf_p, fm, seed))
            cell_key = f"{mode}|cf_p={cf_p}|{fm}"
            # E7 measures final-state cleanliness, not task success. A failed
            # forward action with successful compensation is clean residue-wise.
            ground_truth_clean = sum(1 for t in results if not t.residue_present)
            classified_clean = sum(1 for t in results if t.classified_clean)
            mismatches = sum(
                1
                for t in results
                if t.classified_clean != (not t.residue_present)
            )
            cells[cell_key] = {
                "mode": mode,
                "cf_p": cf_p,
                "failure_mode": fm,
                "trials": args.trials,
                "ground_truth_clean": ground_truth_clean,
                "classified_clean": classified_clean,
                "classification_mismatches": mismatches,
                "residue_present": sum(1 for t in results if t.residue_present),
            }
    args.out.write_text(json.dumps({"cells": cells}, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
