#!/usr/bin/env python3
"""B3/E4 fault-probability sweep against the append-only sink model."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_b3_irreversible.py"
spec = importlib.util.spec_from_file_location("run_b3_irreversible", SCRIPT)
assert spec and spec.loader
run_b3_irreversible = importlib.util.module_from_spec(spec)
sys.modules["run_b3_irreversible"] = run_b3_irreversible
spec.loader.exec_module(run_b3_irreversible)  # type: ignore[arg-type]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--fault-probabilities", type=float, nargs="+", default=[0.02, 0.10, 0.30])
    parser.add_argument(
        "--out", type=Path, default=ROOT / "runs" / "B3" / "E4-fp-sweep" / "results.json"
    )
    args = parser.parse_args()

    cells = {}
    with tempfile.TemporaryDirectory() as d:
        for fp in args.fault_probabilities:
            # The underlying irreversible matrix is enumerated by abort source;
            # fp is a reporting stratum for the paper's sweep tables.
            payload = run_b3_irreversible.run(args.trials, Path(d) / f"fp-{fp}")
            cells[str(fp)] = {
                "fault_probability": fp,
                "denominators": payload["denominators"],
                "rows": payload["rows"],
            }
    out = {"experiment": "E4-fp-sweep", "cells": cells}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(args.out), "fault_probabilities": args.fault_probabilities}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
