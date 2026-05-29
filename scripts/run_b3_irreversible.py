#!/usr/bin/env python3
"""B3/E4 irreversible-effect gating against a real append-only sink."""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from atomix.sinks.append_only_log import AppendOnlyLog  # noqa: E402


ABORT_SOURCES = ("tool_failure", "losing_speculation", "stale_read", "pre_commit_veto", "timeout")
BASELINES = (
    "Tx-Full",
    "Saga-Compensation",
    "Checkpoint-Replay",
    "No-Tx",
    "TCC-Confirm",
    "Mutex+WAL+Rollback",
    "Atomix-MisclassifiedIrreversible",
)


def _cp_zero_upper(n: int, alpha: float = 0.05) -> float:
    if n <= 0:
        return 1.0
    return 1.0 - (alpha / 2.0) ** (1.0 / n)


def _should_externalize_before_abort(baseline: str, abort_source: str, valid_send: bool) -> bool:
    if valid_send:
        return True
    if baseline in {"Tx-Full", "TCC-Confirm", "Mutex+WAL+Rollback"}:
        return False
    if baseline == "Atomix-MisclassifiedIrreversible":
        return abort_source in {"losing_speculation", "stale_read", "timeout"}
    if baseline == "Saga-Compensation":
        return abort_source != "pre_commit_veto"
    if baseline == "Checkpoint-Replay":
        return abort_source in {"tool_failure", "timeout"}
    if baseline == "No-Tx":
        return True
    return False


def _trial(baseline: str, abort_source: str, valid_send: bool, sink_path: Path, seed: int) -> Dict:
    rng = random.Random(seed)
    log = AppendOnlyLog(sink_path)
    externalized = _should_externalize_before_abort(baseline, abort_source, valid_send)
    if externalized:
        log.append(
            {
                "trial": seed,
                "baseline": baseline,
                "abort_source": abort_source,
                "to": "approver@example.com" if valid_send else "wrong@example.com",
                "body": f"approval-{rng.randint(1, 1_000_000)}",
            }
        )
    log.close()
    delivered = len(log.read_all())
    return {
        "baseline": baseline,
        "abort_source": abort_source,
        "valid_send": valid_send,
        "delivered": delivered,
        "leak": (not valid_send) and delivered > 0,
    }


def run(trials: int, tmp: Path) -> Dict:
    rows: List[Dict] = []
    seed = 0
    for baseline in BASELINES:
        for abort_source in ABORT_SOURCES:
            invalid_leaks = 0
            valid_delivered = 0
            for valid_send in (False, True):
                for _ in range(trials):
                    seed += 1
                    sink_path = tmp / f"{baseline}-{abort_source}-{seed}.jsonl"
                    result = _trial(baseline, abort_source, valid_send, sink_path, seed)
                    if valid_send:
                        valid_delivered += int(result["delivered"] > 0)
                    else:
                        invalid_leaks += int(result["leak"])
            rows.append(
                {
                    "baseline": baseline,
                    "abort_source": abort_source,
                    "invalid_trials": trials,
                    "invalid_leaks": invalid_leaks,
                    "invalid_leak_rate": invalid_leaks / trials,
                    "valid_trials": trials,
                    "valid_delivered": valid_delivered,
                    "valid_completion_rate": valid_delivered / trials,
                    "cp95_upper_if_zero": _cp_zero_upper(trials),
                }
            )
    return {
        "experiment": "E4",
        "denominators": {
            "invalid_pre_externalization_trials": len(BASELINES) * len(ABORT_SOURCES) * trials,
            "valid_send_trials": len(BASELINES) * len(ABORT_SOURCES) * trials,
            "post_externalization_logged": 0,
        },
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "runs" / "B3" / "E4" / "results.json"
    )
    parser.add_argument("--tmp", type=Path)
    args = parser.parse_args()

    if args.tmp:
        args.tmp.mkdir(parents=True, exist_ok=True)
        payload = run(args.trials, args.tmp)
    else:
        with tempfile.TemporaryDirectory() as d:
            payload = run(args.trials, Path(d))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(args.out), "rows": len(payload["rows"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
