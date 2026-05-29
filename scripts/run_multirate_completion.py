#!/usr/bin/env python3
"""Run completion experiments at multiple fault rates.

Uses the tasks already discovered at fp=0.3 and runs them at fp=0.1 and fp=0.05.
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
BASE_RESULTS = ATOMIX_ROOT / "results" / "real_completion"
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

MODES = ["Tx-Full", "No-Frontier", "No-Tx"]
VM_PORTS = [5000, 5001, 5002, 5003, 5004]

# Tasks that survived at fp=0.3 (from discovery)
OSWORLD_TASKS = [
    "030eeff7-b492-4218-b312-701ec99ee0cc",
    "13584542-872b-42d8-b299-866967b5c3ef",
    "28cc3b7e-b194-4bc9-8353-d04c0f4d56d2",
    "0326d92d-d218-48a8-9ca1-981cd6d064c7",
    "0b17a146-2934-46c7-8727-73ff6b6483e8",
    "386dbd0e-0241-4a0a-b6a2-6704fba26b1c",
    "00fa164e-2612-4439-992e-157d019a8436",
]

WEBARENA_TASKS = [0, 1, 2, 3, 5, 11, 15, 25, 30, 45]


def run_osworld_task(task_id: str, mode: str, vm_port: int, fault_prob: float, max_steps: int = 50) -> Dict[str, Any]:
    cmd = [
        sys.executable, str(ATOMIX_ROOT / "scripts" / "run_osworld_atomix.py"),
        "--provider", "docker",
        "--task", task_id,
        "--osworld-path", str(OSWORLD_DATA_DIR),
        "--mode", mode,
        "--fault-probability", str(fault_prob),
        "--vm-port", str(vm_port),
        "--vm-host", "localhost",
        "--max-steps", str(max_steps),
    ]
    env = os.environ.copy()
    env["OSWORLD_DATA_DIR"] = str(OSWORLD_DATA_DIR)

    print(f"  [OSWorld] {mode} task={task_id[:8]}... fp={fault_prob}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env, cwd=str(ATOMIX_ROOT))
        try:
            return json.loads(result.stdout.strip())
        except:
            return {"task_id": task_id, "mode": mode, "success": False, "steps_taken": 0, "error": result.stderr[-200:]}
    except subprocess.TimeoutExpired:
        return {"task_id": task_id, "mode": mode, "success": False, "steps_taken": 0, "error": "timeout"}
    except Exception as e:
        return {"task_id": task_id, "mode": mode, "success": False, "steps_taken": 0, "error": str(e)}


def run_webarena_task(task_idx: int, mode: str, fault_prob: float, max_steps: int = 30) -> Dict[str, Any]:
    cmd = [
        sys.executable, str(ATOMIX_ROOT / "scripts" / "run_webarena_atomix.py"),
        "--mode", mode,
        "--fault-probability", str(fault_prob),
        "--test-start-idx", str(task_idx),
        "--test-end-idx", str(task_idx + 1),
        "--model", "gpt-4o",
        "--max-steps", str(max_steps),
        "--result-dir", str(BASE_RESULTS / "webarena"),
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

    print(f"  [WebArena] {mode} task={task_idx} fp={fault_prob}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env, cwd=str(ATOMIX_ROOT))
        try:
            data = json.loads(result.stdout.strip())
            tasks = data.get("tasks", [])
            if tasks:
                t = tasks[0]
                return {
                    "task_idx": task_idx,
                    "mode": mode,
                    "success": t.get("success", False),
                    "faults": t.get("faults", 0),
                    "effects_applied": t.get("effects_applied", 0),
                }
            return {"task_idx": task_idx, "mode": mode, "success": False, "faults": 0, "error": "no results"}
        except:
            return {"task_idx": task_idx, "mode": mode, "success": False, "faults": 0, "error": result.stderr[-200:]}
    except subprocess.TimeoutExpired:
        return {"task_idx": task_idx, "mode": mode, "success": False, "faults": 0, "error": "timeout"}
    except Exception as e:
        return {"task_idx": task_idx, "mode": mode, "success": False, "faults": 0, "error": str(e)}


def run_at_fault_rate(fault_prob: float):
    """Run all tasks at a specific fault rate."""
    results_dir = BASE_RESULTS / f"fp_{int(fault_prob*100):02d}"
    results_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"RUNNING AT FAULT PROBABILITY = {fault_prob}")
    print(f"{'='*60}")

    # OSWorld
    print(f"\n=== OSWorld ({len(OSWORLD_TASKS)} tasks × 3 modes) ===")
    osworld_results: Dict[str, List[Dict]] = {m: [] for m in MODES}

    for mode in MODES:
        print(f"\n--- {mode} ---")
        for i, task_id in enumerate(OSWORLD_TASKS):
            vm_port = VM_PORTS[i % len(VM_PORTS)]
            result = run_osworld_task(task_id, mode, vm_port, fault_prob)
            osworld_results[mode].append(result)
            steps = result.get("steps_taken", 0)
            err = result.get("error", "")
            print(f"    -> steps={steps} err={err[:50] if err else ''}")

    # Summarize OSWorld
    osworld_summary = {}
    for mode in MODES:
        tasks = osworld_results[mode]
        steps_list = [t.get("steps_taken", 0) for t in tasks]
        avg_steps = sum(steps_list) / len(steps_list) if steps_list else 0
        survived = sum(1 for t in tasks if t.get("steps_taken", 0) >= 50 and not t.get("error"))
        osworld_summary[mode] = {
            "total_tasks": len(tasks),
            "survived": survived,
            "avg_steps": avg_steps,
            "steps": steps_list,
            "tasks": tasks,
        }
        print(f"  {mode}: survived={survived}/{len(tasks)} avg_steps={avg_steps:.1f}")

    (results_dir / "osworld_summary.json").write_text(json.dumps(osworld_summary, indent=2))

    # WebArena
    print(f"\n=== WebArena ({len(WEBARENA_TASKS)} tasks × 3 modes) ===")
    webarena_results: Dict[str, List[Dict]] = {m: [] for m in MODES}

    for mode in MODES:
        print(f"\n--- {mode} ---")
        for task_idx in WEBARENA_TASKS:
            result = run_webarena_task(task_idx, mode, fault_prob)
            webarena_results[mode].append(result)
            success = result.get("success", False)
            faults = result.get("faults", 0)
            print(f"    -> {'OK' if success else 'FAIL'} faults={faults}")

    # Summarize WebArena
    webarena_summary = {}
    for mode in MODES:
        tasks = webarena_results[mode]
        completed = sum(1 for t in tasks if t.get("success", False))
        total_faults = sum(t.get("faults", 0) for t in tasks)
        webarena_summary[mode] = {
            "total_tasks": len(tasks),
            "completed": completed,
            "completion_rate": completed / len(tasks) if tasks else 0,
            "total_faults": total_faults,
            "tasks": tasks,
        }
        print(f"  {mode}: completed={completed}/{len(tasks)} faults={total_faults}")

    (results_dir / "webarena_summary.json").write_text(json.dumps(webarena_summary, indent=2))

    return {"osworld": osworld_summary, "webarena": webarena_summary, "fault_prob": fault_prob}


def main():
    print("Multi-Rate Completion Experiments")
    print(f"OSWorld tasks: {len(OSWORLD_TASKS)}")
    print(f"WebArena tasks: {len(WEBARENA_TASKS)}")
    start = time.time()

    all_results = {}
    for fp in [0.1, 0.05]:
        all_results[f"fp_{int(fp*100):02d}"] = run_at_fault_rate(fp)

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"All experiments complete in {elapsed:.0f}s")

    # Combined summary
    combined = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_s": elapsed,
        "results": all_results,
    }
    (BASE_RESULTS / "multirate_summary.json").write_text(json.dumps(combined, indent=2))


if __name__ == "__main__":
    main()
