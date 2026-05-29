"""CLI for the serializability checker.

    python -m atomix.checker LOG.jsonl --substrate filesystem
    python -m atomix.checker LOG.jsonl --substrate taubench --naive-string-scopes
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .serializability import check_log


def main() -> int:
    parser = argparse.ArgumentParser(prog="atomix.checker")
    parser.add_argument("log", type=Path)
    parser.add_argument(
        "--substrate",
        choices=["filesystem", "taubench", "dom", "rw_dep"],
        required=True,
    )
    parser.add_argument(
        "--naive-string-scopes",
        action="store_true",
        help="Disable canonicalization (Tx-NaiveStringScopes ablation)",
    )
    parser.add_argument(
        "--schedules",
        type=int,
        default=None,
        help="Number of schedules contributing to the upper bound (default: count trace_ids)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    args = parser.parse_args()

    result = check_log(
        args.log,
        substrate=args.substrate,
        canonicalize_scopes=not args.naive_string_scopes,
        schedules_checked=args.schedules,
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(
            f"schedules_checked={result.schedules_checked} "
            f"violations_found={result.violations_found} "
            f"upper_bound_95pct={result.upper_bound_95pct:.6f}"
        )
        for c in result.cycles:
            print(f"  cycle: {' -> '.join(c.cycle)} -> {c.cycle[0]}")
            for a, b in c.witness_ops:
                print(f"    {a.op_kind}({a.scope}) by {a.tx_id} -> "
                      f"{b.op_kind}({b.scope}) by {b.tx_id}")
    return 0 if result.violations_found == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
