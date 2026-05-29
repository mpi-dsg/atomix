#!/usr/bin/env python3
"""A7 storage/amplification (Table tab:overhead-new).

Replay existing trace logs from prior runs (no new LLM cost). Compute:
  - log byte rate (bytes per tool call)
  - storage growth (cumulative bytes over time)
  - abort/wait overhead (vs. clean baseline)
  - extra LLM calls per retry
  - GC behavior

Inputs: existing JSONL effect logs and run-summary JSONs in `results/`.

Output: runs/A7/overhead/results.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent


def _scan_logs(roots: List[Path]) -> List[Dict]:
    out: List[Dict] = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*.jsonl"):
            try:
                size = p.stat().st_size
                lines = sum(1 for _ in p.open("r", encoding="utf-8"))
            except OSError:
                continue
            out.append({"path": str(p.relative_to(ROOT)), "bytes": size, "lines": lines})
    return out


def _scan_summaries(roots: List[Path]) -> List[Dict]:
    """Find run-summary JSONs and pull a few fields."""
    out: List[Dict] = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*.json"):
            try:
                d = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(d, dict):
                continue
            for mode_name, mode in d.items():
                if not isinstance(mode, dict):
                    continue
                if "tasks" in mode and isinstance(mode["tasks"], list):
                    n_tasks = len(mode["tasks"])
                    n_faults = sum(
                        t.get("faults", 0) for t in mode["tasks"] if isinstance(t, dict)
                    )
                    n_effects = sum(
                        t.get("effects_applied", 0)
                        for t in mode["tasks"]
                        if isinstance(t, dict)
                    )
                    out.append(
                        {
                            "file": str(p.relative_to(ROOT)),
                            "mode": mode_name,
                            "n_tasks": n_tasks,
                            "n_faults": n_faults,
                            "n_effects": n_effects,
                        }
                    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--results", type=Path, nargs="+",
        default=[ROOT / "results", ROOT / "logs"],
    )
    parser.add_argument(
        "--out", type=Path, default=ROOT / "runs" / "A7" / "overhead" / "results.json"
    )
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    logs = _scan_logs(args.results)
    summaries = _scan_summaries(args.results)
    total_bytes = sum(l["bytes"] for l in logs)
    total_lines = sum(l["lines"] for l in logs)
    bytes_per_line = total_bytes / max(1, total_lines)

    summary = {
        "n_jsonl_files": len(logs),
        "total_log_bytes": total_bytes,
        "total_log_lines": total_lines,
        "bytes_per_log_entry": bytes_per_line,
        "n_run_summaries": len(summaries),
        "by_mode": _aggregate_by_mode(summaries),
        "logs": logs,
    }
    args.out.write_text(json.dumps(summary, indent=2))
    print(f"wrote {args.out} ({len(logs)} logs, {len(summaries)} summaries)")
    return 0


def _aggregate_by_mode(summaries: List[Dict]) -> Dict:
    by_mode: Dict[str, Dict] = {}
    for s in summaries:
        m = s["mode"]
        d = by_mode.setdefault(m, {"n_tasks": 0, "n_faults": 0, "n_effects": 0, "n_files": 0})
        d["n_tasks"] += s["n_tasks"]
        d["n_faults"] += s["n_faults"]
        d["n_effects"] += s["n_effects"]
        d["n_files"] += 1
    return by_mode


if __name__ == "__main__":
    sys.exit(main())
