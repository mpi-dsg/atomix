#!/usr/bin/env python3
"""B5 port comparison manifest for Temporal Saga and SagaLLM."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "runs" / "B5" / "ports" / "results.json"
    )
    args = parser.parse_args()
    rows = [
        {
            "port": "Temporal-Saga",
            "substrates": ["E2 multi-agent subset", "E4 irreversible subset"],
            "status": "mechanism-covered-by Saga-Compensation baseline",
            "requires_real_run": True,
        },
        {
            "port": "SagaLLM",
            "substrates": ["E2 multi-agent subset"],
            "status": "artifact-check-required; fallback reimplementation budgeted",
            "requires_real_run": True,
        },
    ]
    payload = {"experiment": "B5-ports", "rows": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(args.out), "rows": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
