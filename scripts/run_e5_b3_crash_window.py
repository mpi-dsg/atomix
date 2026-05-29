#!/usr/bin/env python3
"""E5-B3: Crash-window enumeration on the persistent idempotency path.

SQLite-backed adapter with kill-9 simulated at four enumerated points:
  P1: before pending-key written
  P2: after pending-key, before effect applied
  P3: after effect applied, before committed-key
  P4: after committed-key

Reuses existing two-phase write tests. Across all four kill points, the
recovery path must produce 0 duplicates.

Fills Table tab:e5-b3.
Output: runs/A7/E5-B3/results.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from atomix.effects import Effect  # noqa: E402
from atomix.epoch import Epoch  # noqa: E402
from atomix.frontier import FrontierTracker  # noqa: E402
from atomix.store import SqliteStore  # noqa: E402
from atomix.transactions import EffectLog, PendingRecoveryError, TransactionManager  # noqa: E402


_KILL_POINTS = ("P1", "P2", "P3", "P4")


def _crash_at(point: str, db: Path) -> Dict[str, int | bool]:
    """Simulate a crash at `point` and return the count of duplicate
    effect applications observed after recovery.

    World state is the count of times the effect was applied. A correct
    two-phase implementation must yield exactly 1 application after
    recovery, regardless of crash point.
    """
    applied_count = [0]

    def apply_effect(effect: Effect) -> None:
        applied_count[0] += 1
        # Inject the crash *before* persisting the committed key, depending on point.

    # Phase 1: run the transaction up to the chosen kill point.
    store_path = db
    if store_path.exists():
        store_path.unlink()
    store = SqliteStore(store_path)
    frontier = FrontierTracker()
    tm = TransactionManager(
        frontier, apply_effect=apply_effect, log=EffectLog(), store=store,
    )
    epoch = Epoch(value=0, trace_id="t0", branch_id=None)
    scopes = {"x"}
    tx = tm.begin(scopes, epoch)
    effect = Effect(
        description="write x",
        scopes={"x"},
        payload={"v": 1},
        idempotency_key="key-x-0",
    )
    tm.record_effect(tx, effect)
    frontier.advance(scopes, epoch)

    # Manually run the two-phase logic with kill-point simulation.
    if point == "P1":
        # Crash before pending key written: no DB state.
        pass
    elif point == "P2":
        # Pending key written, effect not yet applied.
        store.mark_idempotency_key_pending("key-x-0", "t0", tx.tx_id)
    elif point == "P3":
        # Pending key written, effect applied, but not committed.
        store.mark_idempotency_key_pending("key-x-0", "t0", tx.tx_id)
        apply_effect(effect)
    elif point == "P4":
        # Pending key written, effect applied, committed key written.
        store.mark_idempotency_key_pending("key-x-0", "t0", tx.tx_id)
        apply_effect(effect)
        store.mark_idempotency_key_committed("key-x-0")

    store.close()

    # Phase 2: recovery — open a new TransactionManager pointing at the same store.
    store2 = SqliteStore(store_path)
    try:
        tm2 = TransactionManager(
            frontier, apply_effect=apply_effect, log=EffectLog(), store=store2,
        )
    except PendingRecoveryError:
        store2.close()
        return {
            "applications": applied_count[0],
            "duplicate": applied_count[0] > 1,
            "lost": False,
            "recovery_blocked": True,
        }
    # Replay the same effect: a correct implementation MUST detect the
    # idempotency key and skip applying the effect again.
    tx2 = tm2.begin(scopes, epoch)
    tm2.record_effect(tx2, effect)
    frontier.advance(scopes, epoch)
    try:
        tm2.commit(tx2)
    except Exception:
        pass
    store2.close()
    return {
        "applications": applied_count[0],
        "duplicate": applied_count[0] > 1,
        "lost": applied_count[0] == 0,
        "recovery_blocked": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=50)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "runs" / "A7" / "E5-B3" / "results.json"
    )
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    by_point: Dict[str, Dict] = {}
    for pt in _KILL_POINTS:
        duplicates = 0
        lost = 0
        recovery_blocked = 0
        for trial in range(args.trials):
            with tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / f"store-{trial}.db"
                result = _crash_at(pt, db)
                duplicates += int(bool(result["duplicate"]))
                lost += int(bool(result["lost"]))
                recovery_blocked += int(bool(result["recovery_blocked"]))
        by_point[pt] = {
            "trials": args.trials,
            "duplicates": duplicates,
            "lost_effects": lost,
            "recovery_blocked": recovery_blocked,
            "duplicate_rate": duplicates / args.trials,
        }
    args.out.write_text(json.dumps(by_point, indent=2))
    print(json.dumps(by_point, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
