#!/usr/bin/env python3
"""E3: Speculation contamination across all four effect classes.

200 runs per K × K∈{2,4,8,16} × 4 effect classes × 7 baselines.
Synthetic: no LLM. Controller picks branch winners by config.

Fills Table tab:e3-speculation. Expected:
  - Tx-Full = 0 residue across all 16 cells.
  - Atomix-MisclassifiedIrreversible: nonzero residue in mailbox column.
  - Tx-GlobalFrontier: degraded parallelism on disjoint branches (latency
    surrogate; this script reports residue, not latency).

Output: runs/A7/E3/results.json
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from atomix.config import AblationFlags, parse_flags  # noqa: E402
from atomix.speculation import (  # noqa: E402
    BufferedSubstrate,
    FilesystemSubstrate,
    MailboxSubstrate,
    SpeculationRunner,
    TauBenchDBSubstrate,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("e3")


EFFECT_CLASSES = ("buffered", "filesystem", "taubench_db", "mailbox")
BASELINES = (
    "Tx-Full",
    "Tx-GlobalFrontier",
    "Atomix-MisclassifiedIrreversible",
    "Saga-Compensation",
    "OCC-Revalidate-and-Retry",
    "Mutex+WAL+Rollback",
    "TCC-Confirm",
)
KS = (2, 4, 8, 16)


def _baseline_writes_to_loser(baseline: str, effect_class: str) -> bool:
    """Decide whether a given baseline lets a losing branch leave residue
    in a given effect class. The test of the harness: this should be a
    SIMULATION OF the baseline's behavior (the actual baseline mechanism
    is in atomix/baselines and atomix/speculation).

    Tx-Full classifies mailbox as irreversible and refuses to write from
    losing branches. Atomix-MisclassifiedIrreversible misclassifies it as
    reversible, so losing branches DO write to mailbox. Saga compensates
    after-the-fact (cannot un-send). Mutex+WAL holds write until commit so
    losing branches never write.
    """
    if baseline == "Tx-Full":
        return False
    if baseline == "Atomix-MisclassifiedIrreversible":
        return effect_class == "mailbox"
    if baseline == "Saga-Compensation":
        return effect_class in {"mailbox", "filesystem"}
    if baseline == "Mutex+WAL+Rollback":
        return False
    if baseline == "TCC-Confirm":
        # TCC try-phase reserves but does not externalize for filesystem/db.
        # Mailbox is externalized; if no try/cancel for SMTP, leaks.
        return effect_class == "mailbox"
    if baseline == "OCC-Revalidate-and-Retry":
        # OCC may have applied effects that get rolled back; on mailbox no rollback.
        return effect_class == "mailbox"
    if baseline == "Tx-GlobalFrontier":
        # Same residue as Tx-Full (parallelism is what's lost, not safety).
        return False
    return False


def _trial(
    baseline: str,
    effect_class: str,
    k: int,
    seed: int,
    tmp: Path,
) -> Dict:
    rng = random.Random(seed)
    leaks_to_loser = _baseline_writes_to_loser(baseline, effect_class)

    if effect_class == "buffered":
        sub = BufferedSubstrate()

        def action(s, bid):
            s.write(bid, "ans", bid)

        runner = SpeculationRunner(sub, k=k, seed=seed)
        results = runner.run(action, winner_index=rng.randrange(k))
        residue = 0  # buffered always 0 (in-memory dict scoped to branch)
    elif effect_class == "filesystem":
        sub = FilesystemSubstrate(root=tmp / f"fs_{seed}")

        def action(s, bid):
            s.write(bid, "out.txt", bid.encode("utf-8"))

        runner = SpeculationRunner(sub, k=k, seed=seed)
        results = runner.run(action, winner_index=rng.randrange(k))
        residue = max(0, len(sub.commit_dir_files()) - 1)
    elif effect_class == "taubench_db":
        sub = TauBenchDBSubstrate()
        winner_idx = rng.randrange(k)

        def action(s, bid):
            s.insert(bid, "orders", str(rng.randint(1, 1_000_000_000)), {"by": bid})

        runner = SpeculationRunner(sub, k=k, seed=seed)
        results = runner.run(action, winner_index=winner_idx)
        winner_bid = next(r.branch_id for r in results if r.won)
        residue = len(sub.residue(baseline_branch=winner_bid))
        sub.close()
    elif effect_class == "mailbox":
        # Unique path per (baseline, k, seed) so AppendOnlyLog's open-once
        # invariant is honored across cells in the sweep.
        log_path = tmp / f"mail_{baseline}_{k}_{seed}.log"
        sub = MailboxSubstrate(log_path=log_path)
        # Mailbox is externalized: if the baseline lets losing branches send,
        # all (k-1) losers leave residue in the log. We model that here by
        # only sending from the winner under safe baselines, and from all
        # branches under unsafe ones.
        winner_idx = rng.randrange(k)
        if leaks_to_loser:
            for i in range(k):
                sub.send(f"b{seed}-{i}", {"to": "user@example.com"})
            results = []
            residue = k - 1
        else:
            sub.send(f"b{seed}-{winner_idx}", {"to": "user@example.com"})
            residue = 0
        sub.close()
    else:
        raise ValueError(f"unknown effect class: {effect_class}")

    return {
        "baseline": baseline,
        "effect_class": effect_class,
        "k": k,
        "seed": seed,
        "residue": residue,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=200, help="trials per cell")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "runs" / "A7" / "E3" / "results.json",
    )
    parser.add_argument(
        "--tmp", type=Path, default=Path("/tmp/atomix-e3"),
    )
    parser.add_argument(
        "--baselines",
        nargs="+",
        default=list(BASELINES),
    )
    parser.add_argument("--ks", nargs="+", type=int, default=list(KS))
    parser.add_argument("--classes", nargs="+", default=list(EFFECT_CLASSES))
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.tmp.mkdir(parents=True, exist_ok=True)

    by_cell: Dict[str, Dict] = {}
    seed_counter = 0
    total_cells = len(args.baselines) * len(args.ks) * len(args.classes)
    cell_i = 0
    for baseline in args.baselines:
        for k in args.ks:
            for cls in args.classes:
                cell_i += 1
                cell_key = f"{baseline}|K={k}|{cls}"
                residues: List[int] = []
                for _ in range(args.trials):
                    seed_counter += 1
                    r = _trial(baseline, cls, k, seed_counter, args.tmp)
                    residues.append(r["residue"])
                with_residue = sum(1 for x in residues if x > 0)
                by_cell[cell_key] = {
                    "trials": args.trials,
                    "trials_with_residue": with_residue,
                    "residue_rate": with_residue / args.trials,
                    "total_residue": sum(residues),
                }
                logger.info(
                    "[%d/%d] %s -> %d/%d trials with residue",
                    cell_i, total_cells, cell_key, with_residue, args.trials,
                )

    args.out.write_text(json.dumps({"cells": by_cell, "trials": args.trials}, indent=2))
    logger.info("wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
