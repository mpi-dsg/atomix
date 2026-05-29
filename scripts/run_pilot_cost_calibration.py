#!/usr/bin/env python3
"""Pilot P1 token/cost calibration manifest.

This script records the 27-run calibration matrix and, when supplied with
per-run token logs, emits the Markdown report expected by the execution plan.
Without token logs it writes a ready-to-run manifest and clearly marks the
report as pending real SDK measurements.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
WORKLOADS = ("webarena", "osworld", "taubench")
MODES = ("Tx-Full", "Saga-Compensation", "No-Tx")


def _manifest(tasks_per_workload: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for workload in WORKLOADS:
        for task_idx in range(tasks_per_workload):
            for mode in MODES:
                rows.append(
                    {
                        "workload": workload,
                        "task_id": f"{workload}-pilot-{task_idx}",
                        "mode": mode,
                        "status": "pending",
                    }
                )
    return rows


def _load_token_logs(path: Path | None) -> List[Dict[str, Any]]:
    if path is None or not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("token log must be a JSON array")
    return data


def _summaries(logs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for workload in WORKLOADS:
        rows = [r for r in logs if r.get("workload") == workload]
        if not rows:
            continue
        input_tokens = [int(r.get("input_tokens", 0)) for r in rows]
        output_tokens = [int(r.get("output_tokens", 0)) for r in rows]
        calls = [int(r.get("calls", 1)) for r in rows]
        out.append(
            {
                "workload": workload,
                "runs": len(rows),
                "mean_input_tokens": statistics.mean(input_tokens),
                "p95_input_tokens": sorted(input_tokens)[int(0.95 * (len(input_tokens) - 1))],
                "mean_output_tokens": statistics.mean(output_tokens),
                "p95_output_tokens": sorted(output_tokens)[int(0.95 * (len(output_tokens) - 1))],
                "mean_calls": statistics.mean(calls),
            }
        )
    return out


def _write_report(path: Path, manifest: List[Dict[str, Any]], summaries: List[Dict[str, Any]]) -> None:
    lines = ["# Pilot P1 Cost Calibration", ""]
    if not summaries:
        lines.extend(
            [
                "Status: pending real SDK token measurements.",
                "",
                f"Prepared runs: {len(manifest)} (3 workloads x 3 tasks x 3 modes).",
                "",
                "| Workload | Task ID | Mode | Status |",
                "|---|---|---|---|",
            ]
        )
        for row in manifest:
            lines.append(
                f"| {row['workload']} | {row['task_id']} | {row['mode']} | {row['status']} |"
            )
    else:
        lines.extend(
            [
                "| Workload | Runs | Mean input | p95 input | Mean output | p95 output | Mean calls |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in summaries:
            lines.append(
                f"| {row['workload']} | {row['runs']} | {row['mean_input_tokens']:.1f} | "
                f"{row['p95_input_tokens']} | {row['mean_output_tokens']:.1f} | "
                f"{row['p95_output_tokens']} | {row['mean_calls']:.1f} |"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-per-workload", type=int, default=3)
    parser.add_argument("--token-log", type=Path)
    parser.add_argument(
        "--manifest-out", type=Path, default=ROOT / "pilot" / "cost-calibration-manifest.json"
    )
    parser.add_argument(
        "--report-out", type=Path, default=ROOT / "pilot" / "cost-calibration-report.md"
    )
    args = parser.parse_args()

    manifest = _manifest(args.tasks_per_workload)
    logs = _load_token_logs(args.token_log)
    summaries = _summaries(logs)
    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _write_report(args.report_out, manifest, summaries)
    print(json.dumps({"manifest": str(args.manifest_out), "report": str(args.report_out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
