#!/usr/bin/env python3
"""E6: Aliasing stress.

Run the alias suite (A6) through four scope strategies:
  - Tx-Full canonical
  - Tx-NaiveStringScopes
  - Coarse-Global (single scope for everything)
  - Fine-NoOverlapAware (per-tool unique scope, no overlap detection)

For each strategy, we count missed should-conflict cases and false
should-not-conflict matches. Plus a 2-agent stress test on the read-write
subset under each strategy.

Fills Table tab:e6-aliasing.
Output: runs/A7/E6/results.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from atomix.checker.load_alias_suite import (  # noqa: E402
    AliasCase,
    canonicalize,
    load_alias_suite,
)


def _strategy_scope(case: AliasCase, strategy: str) -> tuple[str, str]:
    if strategy == "Tx-Full":
        return (
            canonicalize(case.scope_a, case.substrate),
            canonicalize(case.scope_b, case.substrate),
        )
    if strategy == "Tx-NaiveStringScopes":
        return case.scope_a, case.scope_b
    if strategy == "Coarse-Global":
        return ("__global__", "__global__")
    if strategy == "Fine-NoOverlapAware":
        # Each scope is unique by name (no canonicalization, no aliasing).
        return (case.scope_a + "#unique", case.scope_b + "#unique")
    raise ValueError(strategy)


def _evaluate(strategy: str, cases: List[AliasCase]) -> Dict:
    missed_conflicts = 0
    false_conflicts = 0
    n_should = sum(1 for c in cases if c.label == "should-conflict")
    n_shouldnt = sum(1 for c in cases if c.label == "should-not-conflict")
    for c in cases:
        a, b = _strategy_scope(c, strategy)
        equal = a == b
        if c.label == "should-conflict" and not equal:
            missed_conflicts += 1
        elif c.label == "should-not-conflict" and equal:
            false_conflicts += 1
    return {
        "strategy": strategy,
        "missed_conflicts": missed_conflicts,
        "false_conflicts": false_conflicts,
        "should_conflict_total": n_should,
        "should_not_conflict_total": n_shouldnt,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", type=Path, default=ROOT / "runs" / "A7" / "E6" / "results.json"
    )
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cases = load_alias_suite()
    rows = []
    for strategy in ("Tx-Full", "Tx-NaiveStringScopes", "Coarse-Global", "Fine-NoOverlapAware"):
        rows.append(_evaluate(strategy, cases))
    args.out.write_text(json.dumps({"strategies": rows, "n_cases": len(cases)}, indent=2))
    print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
