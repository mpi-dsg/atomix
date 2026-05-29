#!/usr/bin/env python3
"""Track-B sweep orchestrator.

Drives the 6-baseline x 3-fp x 3-benchmark matrix against the real harnesses.

Per-cell:
  - Sets ATOMIX_USAGE_LOG to a per-cell file so cost rolls up cleanly.
  - Invokes the workload runner (run_webarena_atomix / run_osworld_atomix /
    workloads/tau2/run_atomix) with the chosen mode + fp.
  - Aggregates the resulting usage.jsonl into runs/B-track/sweep.json.

Run small first; scale up by passing `--per-cell N` and `--cells <comma-sep>`.

Usage (local):
  uv run python scripts/run_track_b.py --per-cell 5 \\
      --modes Tx-Full No-Tx --benchmarks webarena --fp 0.0

Usage (remote or dedicated benchmark host, with .env loaded):
  set -a && . ./.env && set +a
  uv run python scripts/run_track_b.py --per-cell 5 ...
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from atomix.usage_log import aggregate as aggregate_usage  # noqa: E402


DEFAULT_MODES = (
    "Tx-Full",
    "No-Frontier",
    "No-Tx",
    "Mutex+WAL+Rollback",
    "TCC-Confirm",
    "OCC-Revalidate-and-Retry",
)
DEFAULT_BENCHMARKS = ("webarena", "osworld", "taubench")
DEFAULT_FP_TIERS = (0.02, 0.10, 0.30)


def _data_root() -> Path:
    return Path(os.environ.get("DATA_ROOT", ROOT / "data")).expanduser()


def _run_webarena(mode: str, fp: float, n_tasks: int, max_steps: int, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["ATOMIX_USAGE_LOG"] = str(out_dir / "usage.jsonl")
    cmd = [
        sys.executable, str(ROOT / "scripts" / "run_webarena_atomix.py"),
        "--mode", mode,
        "--fault-probability", str(fp),
        "--test-start-idx", "0",
        "--test-end-idx", str(n_tasks),
        "--model", "gpt-4o",
        "--max-steps", str(max_steps),
        "--result-dir", str(out_dir),
    ]
    t0 = time.time()
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    duration = time.time() - t0
    # WebArena runner writes filename = "webarena_<mode.lower().replace('-', '_')>.json".
    # Match exactly — '+' is preserved in the runner so we preserve it here too.
    # Fall back to scanning the cell dir if the exact filename doesn't match
    # (forward-compat for any future runner naming change).
    summary_file = out_dir / f"webarena_{mode.lower().replace('-', '_')}.json"
    summary = {}
    if not summary_file.exists():
        candidates = sorted(out_dir.glob("webarena_*.json"))
        if candidates:
            summary_file = candidates[0]
    if summary_file.exists():
        try:
            summary = json.loads(summary_file.read_text())
        except Exception:
            pass
    return {
        "exit_code": proc.returncode,
        "duration_s": duration,
        "summary": summary,
        "usage_log": str(out_dir / "usage.jsonl"),
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def _run_osworld(mode: str, fp: float, n_tasks: int, max_steps: int, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["ATOMIX_USAGE_LOG"] = str(out_dir / "usage.jsonl")
    osworld_pool = [
        "030eeff7-b492-4218-b312-701ec99ee0cc",  # chrome
        "13584542-872b-42d8-b299-866967b5c3ef",  # os
        "28cc3b7e-b194-4bc9-8353-d04c0f4d56d2",  # os
        "0326d92d-d218-48a8-9ca1-981cd6d064c7",  # libreoffice_calc
        "0b17a146-2934-46c7-8727-73ff6b6483e8",  # libreoffice_writer
        "386dbd0e-0241-4a0a-b6a2-6704fba26b1c",  # vlc
        "00fa164e-2612-4439-992e-157d019a8436",  # multi_apps
    ][:n_tasks]
    per_task: List[dict] = []
    overall_start = time.time()
    for tid in osworld_pool:
        cmd = [
            sys.executable, str(ROOT / "scripts" / "run_osworld_atomix.py"),
            "--task", tid,
            "--osworld-path", os.environ.get("OSWORLD_DATA_DIR", str(_data_root() / "osworld")),
            "--mode", mode,
            "--fault-probability", str(fp),
            "--max-steps", str(max_steps),
            "--allow-dirty-vm",
            "--output-json", str(out_dir / f"{mode}_{tid[:8]}.jsonl"),
        ]
        t0 = time.time()
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        per_task.append({
            "task_id": tid,
            "exit_code": proc.returncode,
            "duration_s": time.time() - t0,
            "stdout_tail": proc.stdout[-1000:],
            "stderr_tail": proc.stderr[-1000:],
        })
    return {
        "tasks": per_task,
        "duration_s": time.time() - overall_start,
        "usage_log": str(out_dir / "usage.jsonl"),
    }


def _run_taubench(mode: str, fp: float, n_tasks: int, max_steps: int, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["ATOMIX_USAGE_LOG"] = str(out_dir / "usage.jsonl")
    taubench_dir = Path(os.environ.get("TAUBENCH_DIR", _data_root() / "tau2-bench")).expanduser()
    py = taubench_dir / ".venv" / "bin" / "python"
    if not py.exists():
        py = sys.executable
    save_to = out_dir / f"{mode}.jsonl"
    cmd = [
        str(py), str(ROOT / "workloads" / "tau2" / "run_atomix.py"),
        "--domain", "retail",
        "--num-tasks", str(n_tasks),
        "--mode", mode,
        "--num-trials", "1",
        "--max-steps", str(max_steps),
        "--fault-probability", str(fp),
        "--save-to", str(save_to),
    ]
    t0 = time.time()
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    return {
        "exit_code": proc.returncode,
        "duration_s": time.time() - t0,
        "save_to": str(save_to),
        "usage_log": str(out_dir / "usage.jsonl"),
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


_RUNNERS = {
    "webarena": _run_webarena,
    "osworld": _run_osworld,
    "taubench": _run_taubench,
}


def _safe_relative(path: Path, base: Path) -> Path:
    """Return path relative to base if possible; otherwise the original path.

    The orchestrator may be invoked with an --out path outside the repo
    (e.g., /local/atomix-osworld on the remote machine where disk is plentiful).
    """
    try:
        return path.relative_to(base)
    except ValueError:
        return path


def _aggregate_cell(cell_dir: Path) -> dict:
    log = cell_dir / "usage.jsonl"
    if not log.exists():
        return {"total_records": 0, "total_cost": 0.0}
    return aggregate_usage(log)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "runs" / "B-track" / "sweep")
    parser.add_argument("--modes", nargs="+", default=list(DEFAULT_MODES))
    parser.add_argument("--benchmarks", nargs="+", default=list(DEFAULT_BENCHMARKS))
    parser.add_argument("--fp", nargs="+", type=float, default=list(DEFAULT_FP_TIERS))
    parser.add_argument("--per-cell", type=int, default=10, help="Tasks per cell")
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument(
        "--cells", help="Comma-separated <benchmark>:<mode>:<fp> filter"
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    summary_path = args.out / "summary.json"

    cells: List[tuple[str, str, float]] = []
    if args.cells:
        for spec in args.cells.split(","):
            b, m, f = spec.split(":")
            cells.append((b, m, float(f)))
    else:
        for b in args.benchmarks:
            for m in args.modes:
                for f in args.fp:
                    cells.append((b, m, f))

    summary: Dict = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cells": [],
        "total_cost": 0.0,
    }

    for i, (benchmark, mode, fp) in enumerate(cells, 1):
        cell_dir = args.out / benchmark / mode.replace("+", "_") / f"fp{fp}"
        print(f"\n[{i}/{len(cells)}] {benchmark} mode={mode} fp={fp}")
        runner = _RUNNERS[benchmark]
        result = runner(mode, fp, args.per_cell, args.max_steps, cell_dir)
        agg = _aggregate_cell(cell_dir)
        cell_summary = {
            "benchmark": benchmark,
            "mode": mode,
            "fp": fp,
            "cell_dir": str(_safe_relative(cell_dir, ROOT)),
            "result": result,
            "usage": agg,
        }
        summary["cells"].append(cell_summary)
        summary["total_cost"] += agg.get("total_cost", 0.0)
        print(f"   cost=${agg.get('total_cost', 0.0):.4f} duration={result.get('duration_s', 0):.0f}s")
        # Persist after each cell so partial runs are recoverable.
        summary_path.write_text(json.dumps(summary, indent=2))

    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\nDone. {len(cells)} cells, total cost ${summary['total_cost']:.4f}")
    print(f"Summary: {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
