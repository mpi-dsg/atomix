#!/usr/bin/env python3
"""Run REAL experiments for OSWorld and WebArena with Atomix.

Runs 10 tasks × 3 modes × fault_probability=0.3 for each workload.
Uses real Claude agent for OSWorld, real Playwright browser for WebArena.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ATOMIX_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ATOMIX_ROOT / "results" / "real_experiments"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
DATA_ROOT = Path(os.environ.get("DATA_ROOT", ATOMIX_ROOT / "data")).expanduser()
OSWORLD_DATA_DIR = Path(os.environ.get("OSWORLD_DATA_DIR", DATA_ROOT / "osworld")).expanduser()
WEBARENA_DATA_DIR = Path(os.environ.get("WEBARENA_DATA_DIR", DATA_ROOT / "webarena")).expanduser()

# Load API keys
ENV_FILE = Path(os.environ.get("ATOMIX_ENV", ATOMIX_ROOT / ".env"))
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")

FAULT_PROB = 0.3
MODES = ["Tx-Full", "No-Frontier", "No-Tx"]

# OSWorld: 10 tasks from different domains
OSWORLD_TASKS = [
    "030eeff7-b492-4218-b312-701ec99ee0cc",  # chrome
    "13584542-872b-42d8-b299-866967b5c3ef",  # os
    "045bf3ff-9077-4b86-b483-a1040a949cff",  # gimp
    "01b269ae-2111-4a07-81fd-3fcd711993b0",  # libreoffice_calc
    "0810415c-bde4-4443-9047-d5f70165a697",  # libreoffice_writer
    "0512bb38-d531-4acf-9e7e-0add90816068",  # vs_code
    "215dfd39-f493-4bc3-a027-8a97d72c61bf",  # vlc
    "08c73485-7c6d-4681-999d-919f5c32dcfa",  # thunderbird
    "04578141-1d42-4146-b9cf-6fab4ce5fd74",  # libreoffice_impress
    "00fa164e-2612-4439-992e-157d019a8436",  # multi_apps
]

# WebArena: 10 tasks from different sites
WEBARENA_TASKS = [0, 10, 30, 50, 100, 150, 200, 300, 400, 800]

# VM ports for OSWorld (round-robin across 5 VMs)
VM_PORTS = [5000, 5001, 5002, 5003, 5004]


def run_osworld_task(task_id: str, mode: str, vm_port: int, output_json: Path) -> Dict[str, Any]:
    """Run a single OSWorld task with real VM and Claude agent."""
    cmd = [
        sys.executable, str(ATOMIX_ROOT / "scripts" / "run_osworld_atomix.py"),
        "--provider", "docker",
        "--task", task_id,
        "--osworld-path", str(OSWORLD_DATA_DIR),
        "--mode", mode,
        "--fault-probability", str(FAULT_PROB),
        "--vm-port", str(vm_port),
        "--vm-host", "localhost",
        "--max-steps", "15",
        "--output-json", str(output_json),
    ]

    env = os.environ.copy()
    env["OSWORLD_DATA_DIR"] = str(OSWORLD_DATA_DIR)

    print(f"  [OSWorld] {mode} task={task_id[:8]}... port={vm_port}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, env=env,
            cwd=str(ATOMIX_ROOT)
        )
        if result.returncode != 0:
            print(f"    STDERR: {result.stderr[-200:]}")
        # Parse multi-line JSON output from stdout
        try:
            return json.loads(result.stdout.strip())
        except (json.JSONDecodeError, IndexError):
            return {"task_id": task_id, "mode": mode, "success": False, "error": result.stderr[-300:]}
    except subprocess.TimeoutExpired:
        return {"task_id": task_id, "mode": mode, "success": False, "error": "timeout"}
    except Exception as e:
        return {"task_id": task_id, "mode": mode, "success": False, "error": str(e)}


def run_webarena_task(task_idx: int, mode: str, output_json: Path) -> Dict[str, Any]:
    """Run a single WebArena task with real browser."""
    cmd = [
        sys.executable, str(ATOMIX_ROOT / "scripts" / "run_webarena_atomix.py"),
        "--mode", mode,
        "--fault-probability", str(FAULT_PROB),
        "--test-start-idx", str(task_idx),
        "--test-end-idx", str(task_idx + 1),
        "--model", "gpt-4o",
        "--result-dir", str(RESULTS_DIR / "webarena"),
    ]

    env = os.environ.copy()
    env["WEBARENA_DATA_DIR"] = str(WEBARENA_DATA_DIR)
    env.setdefault("SHOPPING", "http://localhost:7770")
    env.setdefault("SHOPPING_ADMIN", "http://localhost:7780")
    env.setdefault("REDDIT", "http://localhost:9999")
    env.setdefault("GITLAB", "http://localhost:8023")
    env.setdefault("WIKIPEDIA", "http://localhost:8888")
    env.setdefault("MAP", "http://localhost:3000")
    env.setdefault("HOMEPAGE", "http://localhost:4399")

    print(f"  [WebArena] {mode} task_idx={task_idx}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, env=env,
            cwd=str(ATOMIX_ROOT)
        )
        if result.returncode != 0:
            print(f"    STDERR: {result.stderr[-300:]}")
        try:
            return json.loads(result.stdout.strip())
        except (json.JSONDecodeError, IndexError):
            return {"task_idx": task_idx, "mode": mode, "success_rate": 0, "error": result.stderr[-300:]}
    except subprocess.TimeoutExpired:
        return {"task_idx": task_idx, "mode": mode, "success_rate": 0, "error": "timeout"}
    except Exception as e:
        return {"task_idx": task_idx, "mode": mode, "success_rate": 0, "error": str(e)}


def run_osworld_experiments() -> Dict[str, Any]:
    """Run all OSWorld experiments."""
    print("\n=== OSWorld Real Experiments (10 tasks × 3 modes) ===")
    all_results: Dict[str, List[Dict]] = {"Tx-Full": [], "No-Frontier": [], "No-Tx": []}

    for mode in MODES:
        print(f"\n--- Mode: {mode} ---")
        output_json = RESULTS_DIR / f"osworld_{mode.lower().replace('-','_')}.jsonl"
        if output_json.exists():
            output_json.unlink()

        for i, task_id in enumerate(OSWORLD_TASKS):
            vm_port = VM_PORTS[i % len(VM_PORTS)]
            result = run_osworld_task(task_id, mode, vm_port, output_json)
            all_results[mode].append(result)
            success = result.get("success", False)
            print(f"    -> {'OK' if success else 'FAIL'} (steps={result.get('steps_taken', '?')})")

    # Summary
    summary = {}
    for mode in MODES:
        successes = sum(1 for r in all_results[mode] if r.get("success"))
        summary[mode] = {
            "total": len(all_results[mode]),
            "successes": successes,
            "success_rate": successes / max(len(all_results[mode]), 1),
            "tasks": all_results[mode],
        }

    # Write summary
    out_file = RESULTS_DIR / "osworld_summary.json"
    out_file.write_text(json.dumps(summary, indent=2))
    print(f"\nOSWorld Summary:")
    for mode in MODES:
        s = summary[mode]
        print(f"  {mode}: {s['successes']}/{s['total']} ({s['success_rate']*100:.0f}%)")

    return summary


def run_webarena_experiments() -> Dict[str, Any]:
    """Run all WebArena experiments."""
    print("\n=== WebArena Real Experiments (10 tasks × 3 modes) ===")
    all_results: Dict[str, List[Dict]] = {"Tx-Full": [], "No-Frontier": [], "No-Tx": []}

    for mode in MODES:
        print(f"\n--- Mode: {mode} ---")
        for task_idx in WEBARENA_TASKS:
            output_json = RESULTS_DIR / f"webarena_{mode.lower().replace('-','_')}_{task_idx}.json"
            result = run_webarena_task(task_idx, mode, output_json)
            all_results[mode].append(result)
            sr = result.get("success_rate", 0)
            print(f"    -> success_rate={sr:.0%}")

    # Summary
    summary = {}
    for mode in MODES:
        rates = [r.get("success_rate", 0) for r in all_results[mode]]
        successes = sum(1 for r in rates if r > 0)
        summary[mode] = {
            "total": len(all_results[mode]),
            "successes": successes,
            "avg_success_rate": sum(rates) / max(len(rates), 1),
            "tasks": all_results[mode],
        }

    out_file = RESULTS_DIR / "webarena_summary.json"
    out_file.write_text(json.dumps(summary, indent=2))
    print(f"\nWebArena Summary:")
    for mode in MODES:
        s = summary[mode]
        print(f"  {mode}: {s['successes']}/{s['total']} (avg_rate={s['avg_success_rate']*100:.0f}%)")

    return summary


def main():
    print(f"Atomix Real Experiments")
    print(f"Fault probability: {FAULT_PROB}")
    print(f"Results dir: {RESULTS_DIR}")
    start = time.time()

    osworld_summary = run_osworld_experiments()
    webarena_summary = run_webarena_experiments()

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"All experiments complete in {elapsed:.0f}s")
    print(f"\nFinal Results:")
    print(f"  OSWorld:  Tx-Full={osworld_summary['Tx-Full']['success_rate']*100:.0f}%  "
          f"No-Frontier={osworld_summary['No-Frontier']['success_rate']*100:.0f}%  "
          f"No-Tx={osworld_summary['No-Tx']['success_rate']*100:.0f}%")
    print(f"  WebArena: Tx-Full={webarena_summary['Tx-Full']['avg_success_rate']*100:.0f}%  "
          f"No-Frontier={webarena_summary['No-Frontier']['avg_success_rate']*100:.0f}%  "
          f"No-Tx={webarena_summary['No-Tx']['avg_success_rate']*100:.0f}%")

    # Write combined summary
    combined = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "fault_probability": FAULT_PROB,
        "duration_s": elapsed,
        "osworld": osworld_summary,
        "webarena": webarena_summary,
    }
    (RESULTS_DIR / "combined_summary.json").write_text(json.dumps(combined, indent=2))


if __name__ == "__main__":
    main()
