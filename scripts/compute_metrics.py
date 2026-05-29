#!/usr/bin/env python3
"""Compute summary metrics from Atomix workload results."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _flatten_results(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    flattened: List[Dict[str, Any]] = []
    for entry in raw:
        if "atomix" in entry or "baseline" in entry:
            for key in ("atomix", "baseline"):
                if key in entry and isinstance(entry[key], dict):
                    flattened.append(entry[key])
        elif "mode" in entry:
            flattened.append(entry)
    return flattened


def _summarize(entries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    by_mode: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        mode = entry.get("mode", "unknown")
        by_mode.setdefault(mode, []).append(entry)

    summary: Dict[str, Any] = {}
    for mode, items in by_mode.items():
        total = len(items)
        success = sum(1 for item in items if item.get("success"))
        partial = sum(1 for item in items if item.get("partial_state"))
        durations = [item.get("duration_ms", 0.0) for item in items]
        effects = [item.get("effects_applied", 0) for item in items]
        summary[mode] = {
            "total": total,
            "success": success,
            "success_rate": success / total if total else 0.0,
            "partial": partial,
            "avg_duration_ms": sum(durations) / total if total else 0.0,
            "avg_effects": sum(effects) / total if total else 0.0,
        }
    return summary


def _flatten_for_csv(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for entry in entries:
        row = dict(entry)
        row["mode"] = entry.get("mode", "unknown")
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Path to JSON results")
    parser.add_argument("--output", help="Optional output path for JSON summary")
    parser.add_argument("--csv", help="Optional output path for CSV rows")
    args = parser.parse_args()

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    flattened = _flatten_results(raw if isinstance(raw, list) else [])
    summary = _summarize(flattened)

    if args.output:
        Path(args.output).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    else:
        print(json.dumps(summary, indent=2))

    if args.csv:
        rows = _flatten_for_csv(flattened)
        if rows:
            with Path(args.csv).open("w", encoding="utf-8", newline="") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=sorted(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
        else:
            Path(args.csv).write_text("", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
