#!/usr/bin/env python3
"""E5-B1: semantic-validation boundary negative control.

This controlled runner shows the paper's intended non-claim: Atomix's
transactional machinery does not infer business semantics by itself. A
semantic hook must reject wrong recipients / wrong amounts before the effect is
recorded.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class SemanticTask:
    task_id: str
    expected_recipient: str
    expected_amount_cents: int
    proposed_recipient: str
    proposed_amount_cents: int

    @property
    def semantically_valid(self) -> bool:
        return (
            self.expected_recipient == self.proposed_recipient
            and self.expected_amount_cents == self.proposed_amount_cents
        )


def _make_tasks(n_tasks: int, seed: int) -> List[SemanticTask]:
    rng = random.Random(seed)
    tasks: List[SemanticTask] = []
    recipients = [f"user{i}@example.com" for i in range(max(3, n_tasks))]
    for idx in range(n_tasks):
        expected = recipients[idx % len(recipients)]
        amount = 10_00 + idx * 137
        proposed_recipient = expected
        proposed_amount = amount
        error_kind = idx % 4
        if error_kind == 1:
            proposed_recipient = rng.choice([r for r in recipients if r != expected])
        elif error_kind == 2:
            proposed_amount = amount + 500
        elif error_kind == 3:
            proposed_recipient = rng.choice([r for r in recipients if r != expected])
            proposed_amount = amount + 500
        tasks.append(
            SemanticTask(
                task_id=f"semantic-{idx:03d}",
                expected_recipient=expected,
                expected_amount_cents=amount,
                proposed_recipient=proposed_recipient,
                proposed_amount_cents=proposed_amount,
            )
        )
    return tasks


def _run_mode(mode: str, tasks: List[SemanticTask]) -> Dict:
    use_hook = mode == "Tx-Full+SemanticHook"
    if mode not in {"Tx-Full", "Tx-Full+SemanticHook"}:
        raise ValueError(f"unsupported mode {mode}")

    committed: List[SemanticTask] = []
    rejected = 0
    for task in tasks:
        if use_hook and not task.semantically_valid:
            rejected += 1
            continue
        committed.append(task)

    invalid_commits = sum(1 for task in committed if not task.semantically_valid)
    valid_tasks = sum(1 for task in tasks if task.semantically_valid)
    valid_commits = sum(1 for task in committed if task.semantically_valid)
    return {
        "mode": mode,
        "tasks": len(tasks),
        "valid_tasks": valid_tasks,
        "commits": len(committed),
        "valid_commits": valid_commits,
        "semantic_invalid_commits": invalid_commits,
        "rejected_by_semantic_hook": rejected,
        "valid_completion_rate": valid_commits / max(1, valid_tasks),
        "invalid_commit_rate": invalid_commits / max(1, len(tasks) - valid_tasks),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=30)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "runs" / "B7" / "E5-B1" / "results.json",
    )
    args = parser.parse_args()

    tasks = _make_tasks(args.tasks, args.seed)
    rows = [_run_mode(mode, tasks) for mode in ("Tx-Full", "Tx-Full+SemanticHook")]
    payload = {
        "experiment": "E5-B1",
        "description": "Semantic-invalid commits require a semantic hook; Tx-Full alone does not reject them.",
        "tasks": [asdict(task) for task in tasks],
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"rows": rows, "out": str(args.out)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
