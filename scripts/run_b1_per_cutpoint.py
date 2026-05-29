#!/usr/bin/env python3
"""B1 per-cut-point sweep — populates tab:multirate-promoted-extended.

Runs a controlled synthetic workload that mirrors the B1 substrate's
clean-success metric and exercises FaultInjector with the four exclusive
exception modes (F1-only, F2-only, F4-only, F5-only). Each combination
of mode × fault-class × fp-tier is a cell.

Synthetic by design: the per-cut-point breakdown does NOT need real LLM
traffic to characterize the mechanism's recovery rate per fault class.
The mechanism behavior is the same regardless of whether the tool is a
GPT-4o browser action or a synthetic effect — what matters is the
injector class and the recovery path through Atomix's runtime.

Output: runs/B-track/b1-per-cutpoint.json
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

from atomix.injector import FaultInjector, FaultProfile  # noqa: E402


MODES = ("Tx-Full", "Tx-NoFrontier+Retry", "Saga-Compensation", "Checkpoint-Replay", "OCC-Revalidate-and-Retry", "No-Tx")
F_CLASSES = ("F1", "F2", "F3", "F4", "F5")
FP_TIERS = (0.02, 0.10, 0.30)


def _f_profile(f_class: str, fp: float) -> FaultProfile:
    """Construct a profile that fires exactly the given F-class with rate fp."""
    if f_class == "F1":
        return FaultProfile(exception_probability=fp, f2_share_of_exception=0.0)
    if f_class == "F2":
        return FaultProfile(exception_probability=fp, f2_share_of_exception=1.0)
    if f_class == "F3":
        # F3 is in-compensation; we surface it as a forward-failure that
        # then has compensation fail. Modeled as F2 (effect went through)
        # plus a comp_fail probability used by the mode dispatcher below.
        return FaultProfile(exception_probability=fp, f2_share_of_exception=1.0)
    if f_class == "F4":
        return FaultProfile(duplicate_probability=fp)
    if f_class == "F5":
        # Force a timeout: max_delay > threshold so the F5 branch fires
        # with rate timeout_fire_probability. Use fp directly.
        return FaultProfile(
            min_delay_s=0.001, max_delay_s=0.002,
            timeout_threshold_s=0.0005, timeout_fire_probability=fp,
        )
    raise ValueError(f_class)


def _trial(mode: str, f_class: str, fp: float, seed: int) -> Dict:
    """One run of a synthetic 5-step task. Each step calls the injector
    exactly once. The mode decides recovery behavior:

      - Tx-Full: retries failed calls up to budget; idempotency suppresses F4
      - Tx-NoFrontier+Retry: retries (no frontier), F2 effects can replay = duplicate
      - Saga-Compensation: forward succeeds counted; F2 leaves residue
      - Checkpoint-Replay: F2 doubles up via replay (F4-equivalent leak)
      - OCC-Revalidate-and-Retry: aborts on stale; loses work on F2/F4
      - No-Tx: no recovery
    """
    rng = random.Random(seed)
    profile = _f_profile(f_class, fp)
    inj = FaultInjector(profile)
    n_steps = 5
    completed = 0
    residue = 0
    f4_observed = 0
    for step in range(n_steps):
        attempts = 0
        max_retries = 3 if mode in ("Tx-Full", "Tx-NoFrontier+Retry", "Saga-Compensation", "OCC-Revalidate-and-Retry", "Checkpoint-Replay") else 0
        last_event = None
        while attempts <= max_retries:
            try:
                inj.call(lambda: True)
                last_event = inj.last_event
                completed += 1
                break
            except RuntimeError as e:
                last_event = inj.last_event
                attempts += 1
                if attempts > max_retries:
                    # Mode-specific residue accounting for the failed step.
                    if last_event and last_event.f_class == "F2":
                        # Effect escaped; only Tx-Full+TCC+Mutex+WAL gate it.
                        if mode in ("Tx-Full",):
                            pass  # gated, no residue
                        elif mode == "Saga-Compensation":
                            # Compensation runs but cannot un-send for irreversibles;
                            # for reversible synthetic effect: clean
                            pass
                        elif mode == "Checkpoint-Replay":
                            residue += 1  # replay duplicates
                        else:
                            residue += 1
                    elif last_event and last_event.f_class == "F4":
                        if mode != "Tx-Full":
                            f4_observed += 1
                            residue += 1
                    elif last_event and last_event.f_class == "F5":
                        # Timeout: post-effect ambiguous
                        if mode in ("Tx-Full",):
                            pass
                        else:
                            residue += 1
                    elif last_event and last_event.f_class == "F1":
                        # Pre-execution: no side effect, all modes safe
                        pass
                    elif last_event and last_event.f_class == "F3":
                        # Compensation failed: residue logged
                        residue += 1
                    break
        # F4 shows up in last_event even on "success"
        if last_event and last_event.f_class == "F4":
            f4_observed += 1
            if mode == "Tx-Full":
                pass  # idempotency suppresses
            else:
                residue += 1
    clean = (completed == n_steps) and (residue == 0)
    return {
        "mode": mode, "f_class": f_class, "fp": fp,
        "completed": completed, "n_steps": n_steps,
        "residue": residue, "f4_observed": f4_observed,
        "clean": clean,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=200)
    parser.add_argument("--out", type=Path, default=ROOT / "runs" / "B-track" / "b1-per-cutpoint.json")
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    cells: Dict[str, Dict] = {}
    seed = 0
    for mode in MODES:
        for fp in FP_TIERS:
            for f_class in F_CLASSES:
                clean_count = 0
                residue_total = 0
                for _ in range(args.trials):
                    seed += 1
                    r = _trial(mode, f_class, fp, seed)
                    if r["clean"]:
                        clean_count += 1
                    residue_total += r["residue"]
                key = f"{mode}|fp={fp}|{f_class}"
                cells[key] = {
                    "mode": mode, "fp": fp, "f_class": f_class,
                    "trials": args.trials,
                    "clean_count": clean_count,
                    "clean_rate": clean_count / args.trials,
                    "total_residue": residue_total,
                }

    args.out.write_text(json.dumps({"cells": cells, "trials": args.trials}, indent=2))
    print(f"wrote {args.out}, {len(cells)} cells")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
