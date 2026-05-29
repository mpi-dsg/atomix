#!/usr/bin/env python3
"""E10 annotation cost: count LOC per adapter from the prototype source.

Reports per-adapter LOC of the scope/effect annotations vs. the underlying
tool implementation. Fills Table tab:e10-annotation-cost.

Output: runs/A7/E10/annotation_cost.json
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class AdapterStats:
    name: str
    file: str
    total_loc: int = 0
    scopes_loc: int = 0
    to_effect_loc: int = 0
    other_loc: int = 0


def _classify(file: Path) -> List[AdapterStats]:
    """For each class in `file` that defines `scopes` and `to_effect`, count
    LOC for those methods vs. the rest of the class.
    """
    text = file.read_text()
    tree = ast.parse(text, filename=str(file))
    out: List[AdapterStats] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        method_lines = {}
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_lines[item.name] = (item.lineno, item.end_lineno or item.lineno)
        if "scopes" not in method_lines and "to_effect" not in method_lines:
            continue
        scopes_loc = _loc(method_lines.get("scopes"))
        to_effect_loc = _loc(method_lines.get("to_effect"))
        total_loc = (node.end_lineno or node.lineno) - node.lineno + 1
        out.append(
            AdapterStats(
                name=node.name,
                file=str(file.relative_to(ROOT)),
                total_loc=total_loc,
                scopes_loc=scopes_loc,
                to_effect_loc=to_effect_loc,
                other_loc=max(0, total_loc - scopes_loc - to_effect_loc),
            )
        )
    return out


def _loc(span) -> int:
    if span is None:
        return 0
    return span[1] - span[0] + 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--src", type=Path, default=ROOT / "src" / "atomix"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "runs" / "A7" / "E10" / "annotation_cost.json",
    )
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    all_stats: List[AdapterStats] = []
    for py in args.src.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            all_stats.extend(_classify(py))
        except SyntaxError:
            continue

    summary = {
        "n_adapters": len(all_stats),
        "total_scope_loc": sum(s.scopes_loc for s in all_stats),
        "total_to_effect_loc": sum(s.to_effect_loc for s in all_stats),
        "mean_annotation_loc_per_adapter": (
            sum(s.scopes_loc + s.to_effect_loc for s in all_stats) / max(1, len(all_stats))
        ),
        "adapters": [s.__dict__ for s in all_stats],
    }
    args.out.write_text(json.dumps(summary, indent=2))
    print(f"wrote {args.out} ({summary['n_adapters']} adapters)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
