#!/usr/bin/env python3
"""Aggregate experiment outputs into summary JSON + CSV."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _load_results(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _aggregate_speculation(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[tuple[str, int], List[Dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        mode = str(entry.get("mode", "Tx-Full"))
        grouped[(mode, int(entry.get("k_branches", 0)))].append(entry)
    rows: List[Dict[str, Any]] = []
    for (mode, k), items in sorted(grouped.items()):
        total = len(items)
        transient = sum(1 for item in items if item.get("transient_contamination"))
        end_state = sum(1 for item in items if item.get("end_state_contamination"))
        rows.append(
            {
                "section": "speculation",
                "mode": mode,
                "k_branches": k,
                "runs": total,
                "transient_rate": transient / total if total else 0.0,
                "end_state_rate": end_state / total if total else 0.0,
            }
        )
    return rows


def _aggregate_out_of_order(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        key = (
            str(entry.get("mode", "unknown")),
            str(entry.get("delay_variant", "low")),
        )
        grouped[key].append(entry)
    rows: List[Dict[str, Any]] = []
    for (mode, delay_variant), items in sorted(grouped.items()):
        total_tx = sum(int(item.get("transactions", 0)) for item in items)
        violations = sum(int(item.get("violations", 0)) for item in items)
        rows.append(
            {
                "section": "out_of_order",
                "mode": mode,
                "delay_variant": delay_variant,
                "transactions": total_tx,
                "violation_rate": violations / total_tx if total_tx else 0.0,
            }
        )
    return rows


def _aggregate_microbench(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        key = (
            str(entry.get("experiment", "E3")),
            str(entry.get("mode", "unknown")),
            int(entry.get("n_concurrent", 0)),
            entry.get("zipf_alpha"),
            str(entry.get("scenario", "random")),
        )
        grouped[key].append(entry)
    rows: List[Dict[str, Any]] = []

    def _sort_key(item: tuple[tuple, List[Dict[str, Any]]]) -> tuple:
        experiment, mode, n_concurrent, zipf_alpha, scenario = item[0]
        zipf_value = zipf_alpha if zipf_alpha is not None else -1.0
        return (experiment, mode, n_concurrent, zipf_value, scenario)

    for (experiment, mode, n_concurrent, zipf_alpha, scenario), items in sorted(
        grouped.items(), key=_sort_key
    ):
        total = len(items)
        violations = sum(1 for item in items if item.get("violated_invariant"))
        negative_counts = sum(1 for item in items if item.get("min_value", 0) < 0)
        throughput = [
            item.get("result", {}).get("throughput_tx_per_sec", 0.0) for item in items
        ]
        rows.append(
            {
                "section": "microbench",
                "experiment": experiment,
                "mode": mode,
                "n_concurrent": n_concurrent,
                "zipf_alpha": zipf_alpha,
                "scenario": scenario,
                "runs": total,
                "violation_rate": violations / total if total else 0.0,
                "negative_rate": negative_counts / total if total else 0.0,
                "avg_throughput": sum(throughput) / total if total else 0.0,
            }
        )
    return rows


def _aggregate_external(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        workload = str(entry.get("workload", "external"))
        mode = str(entry.get("mode", "Tx-Full"))
        grouped[(workload, mode)].append(entry)
    rows: List[Dict[str, Any]] = []
    for (workload, mode), items in sorted(grouped.items()):
        total = len(items)
        failures = sum(1 for item in items if item.get("summary", {}).get("returncode"))
        durations = [item.get("summary", {}).get("duration_s", 0.0) for item in items]
        rows.append(
            {
                "section": "external",
                "workload": workload,
                "mode": mode,
                "runs": total,
                "failures": failures,
                "avg_duration_s": sum(durations) / total if total else 0.0,
            }
        )
    return rows


def _aggregate_table_rows(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for entry in entries:
        experiment = entry.get("experiment", "unknown")
        denominators = entry.get("denominators", {})
        for row in entry.get("rows", []):
            if isinstance(row, dict):
                out = {"section": str(experiment).lower(), "experiment": experiment}
                out.update(denominators if isinstance(denominators, dict) else {})
                out.update(row)
                rows.append(out)
        cells = entry.get("cells", {})
        for fp, cell in (cells.items() if isinstance(cells, dict) else []):
            if isinstance(cell, dict) and "rows" in cell:
                for row in cell.get("rows", []):
                    out = {
                        "section": str(experiment).lower(),
                        "experiment": experiment,
                        "fault_probability": fp,
                    }
                    out.update(row)
                    rows.append(out)
    return rows


def _collect_inputs(paths: Iterable[Path]) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for path in paths:
        raw = _load_results(path)
        if isinstance(raw, dict) and "summary" in raw:
            buckets["external"].append(raw)
        elif isinstance(raw, dict) and ("rows" in raw or "cells" in raw):
            buckets["table_rows"].append(raw)
        elif isinstance(raw, list) and raw and "transient_contamination" in raw[0]:
            buckets["speculation"].extend(raw)
        elif isinstance(raw, list) and raw and "violations" in raw[0]:
            buckets["out_of_order"].extend(raw)
        elif isinstance(raw, list):
            buckets["microbench"].extend(raw)
    return buckets


def run(input_dir: Path, output_json: Path, output_csv: Path | None) -> None:
    paths = list(input_dir.glob("*.json"))
    buckets = _collect_inputs(paths)

    rows: List[Dict[str, Any]] = []
    rows.extend(_aggregate_microbench(buckets.get("microbench", [])))
    rows.extend(_aggregate_out_of_order(buckets.get("out_of_order", [])))
    rows.extend(_aggregate_speculation(buckets.get("speculation", [])))
    rows.extend(_aggregate_external(buckets.get("external", [])))
    rows.extend(_aggregate_table_rows(buckets.get("table_rows", [])))

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")

    if output_csv:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        if rows:
            fieldnames = sorted({key for row in rows for key in row.keys()})
            with output_csv.open("w", encoding="utf-8", newline="") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        else:
            output_csv.write_text("", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir", default="results/experiments", help="Results dir"
    )
    parser.add_argument("--output", required=True, help="Output summary JSON")
    parser.add_argument("--csv", help="Optional output CSV")
    args = parser.parse_args()

    run(Path(args.input_dir), Path(args.output), Path(args.csv) if args.csv else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
