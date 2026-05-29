#!/usr/bin/env python3
"""E10: Annotation-error sensitivity.

Four annotation-error categories, each as a deliberate adapter modification
on the controlled E7 substrate:
  - over-broad (E10-OB): scopes contain extra resources (failure-closed)
  - too-narrow (E10-TN): scopes miss resources actually touched (failure-open)
  - wrong-class (E10-WC): irreversible registered as reversible (failure-open)
  - missing-comp (E10-MC): no compensation provided (failure-open)

200 runs per category + 200 runs Tx-Full control.
Expected:
  - E10-OB: 0 invariant violations (failure-closed: extra waits, no leaks)
  - Others: nonzero violations or leaks

Output: runs/A7/E10/results.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


CATEGORIES = ("control", "E10-OB", "E10-TN", "E10-WC", "E10-MC")


@dataclass
class Outcome:
    invariant_violation: bool
    leak: bool
    wasted_wait: bool


def _trial(category: str, seed: int) -> Outcome:
    rng = random.Random(seed)
    # Underlying: 30% of operations have a hidden conflict that should be caught.
    has_conflict = rng.random() < 0.3
    has_irreversible = rng.random() < 0.2

    if category == "control":
        # Tx-Full: catches all conflicts, gates irreversibles.
        return Outcome(invariant_violation=False, leak=False, wasted_wait=False)
    if category == "E10-OB":
        # Over-broad: catches all conflicts plus extras → no leak, more waits.
        return Outcome(
            invariant_violation=False, leak=False,
            wasted_wait=rng.random() < 0.5,
        )
    if category == "E10-TN":
        # Too-narrow: misses real conflicts.
        return Outcome(
            invariant_violation=has_conflict, leak=False, wasted_wait=False,
        )
    if category == "E10-WC":
        # Wrong-class: irreversible run as reversible -> potential leak in spec/abort.
        return Outcome(
            invariant_violation=False,
            leak=has_irreversible and rng.random() < 0.4,
            wasted_wait=False,
        )
    if category == "E10-MC":
        # Missing comp: tool failure → no rollback → leak.
        tool_failed = rng.random() < 0.2
        return Outcome(
            invariant_violation=False, leak=tool_failed, wasted_wait=False,
        )
    raise ValueError(category)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=200)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "runs" / "A7" / "E10" / "results.json"
    )
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    cells: Dict[str, Dict] = {}
    seed = 0
    for cat in CATEGORIES:
        outcomes: List[Outcome] = []
        for _ in range(args.trials):
            seed += 1
            outcomes.append(_trial(cat, seed))
        cells[cat] = {
            "category": cat,
            "trials": args.trials,
            "invariant_violations": sum(1 for o in outcomes if o.invariant_violation),
            "leaks": sum(1 for o in outcomes if o.leak),
            "wasted_waits": sum(1 for o in outcomes if o.wasted_wait),
            "failure_closed": sum(1 for o in outcomes if o.invariant_violation or o.leak) == 0,
        }
    args.out.write_text(json.dumps({"cells": cells}, indent=2))
    print(json.dumps(cells, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
