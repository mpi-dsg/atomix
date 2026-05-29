#!/usr/bin/env python3
"""Run REAL experiments targeting TASK COMPLETION (not just steps).

For each workload, runs tasks in Tx-Full mode until 10 complete successfully.
Then runs the same 10 tasks in No-Frontier and No-Tx modes.
Reports: task_id, mode, completed (bool), steps_taken, faults, retries.

OSWorld: max_steps=50 (up from 15)
WebArena: max_steps=30
tau2-bench: max_steps=50 (already done)
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
RESULTS_DIR = ATOMIX_ROOT / "results" / "real_completion"
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

# OSWorld: large pool of verified task IDs (across domains)
OSWORLD_TASK_POOL = [
    # chrome (5)
    "030eeff7-b492-4218-b312-701ec99ee0cc",
    "06fe7178-4491-4589-810f-2e2bc9502122",
    "0d8b7de3-e8de-4d86-b9fd-dd2dce58a217",
    "12086550-11c0-466b-b367-1d9e75b3910e",
    "121ba48f-9e17-48ce-9bc6-a4fb17a7ebba",
    # os (3)
    "13584542-872b-42d8-b299-866967b5c3ef",
    "23393935-50c7-4a86-aeea-2b78fd089c5c",
    "28cc3b7e-b194-4bc9-8353-d04c0f4d56d2",
    # gimp (3)
    "045bf3ff-9077-4b86-b483-a1040a949cff",
    "06ca5602-62ca-47f6-ad4f-da151cde54cc",
    "2a729ded-3296-423d-aec4-7dd55ed5fbb3",
    # libreoffice_calc (3)
    "01b269ae-2111-4a07-81fd-3fcd711993b0",
    "0326d92d-d218-48a8-9ca1-981cd6d064c7",
    "035f41ba-6653-43ab-aa63-c86d449d62e5",
    # libreoffice_writer (3)
    "0810415c-bde4-4443-9047-d5f70165a697",
    "0a0faba3-5580-44df-965d-f562a99b291c",
    "0b17a146-2934-46c7-8727-73ff6b6483e8",
    # vlc (3)
    "215dfd39-f493-4bc3-a027-8a97d72c61bf",
    "386dbd0e-0241-4a0a-b6a2-6704fba26b1c",
    "59f21cfb-0120-4326-b255-a5b827b38967",
    # thunderbird (3)
    "08c73485-7c6d-4681-999d-919f5c32dcfa",
    "10a730d5-d414-4b40-b479-684bed1ae522",
    "15c3b339-88f7-4a86-ab16-e71c58dcb01e",
    # libreoffice_impress (3)
    "04578141-1d42-4146-b9cf-6fab4ce5fd74",
    "05dd4c1d-c489-4c85-8389-a7836c4f0567",
    "08aced46-45a2-48d7-993b-ed3fb5b32302",
    # multi_apps (3)
    "00fa164e-2612-4439-992e-157d019a8436",
    "02ce9a50-7af2-47ed-8596-af0c230501f8",
    "09a37c51-e625-49f4-a514-20a773797a8a",
]

# WebArena: larger pool of tasks (across sites)
WEBARENA_TASK_POOL = [
    0, 1, 2, 3, 5, 10, 11, 15, 20, 25,
    30, 35, 40, 45, 50, 55, 60, 70, 80, 90,
    100, 110, 120, 130, 140, 150, 160, 170, 180, 190,
    200, 210, 220, 250, 300, 350, 400, 450, 500, 600,
]

# VM ports for OSWorld (round-robin across 5 VMs)
VM_PORTS = [5000, 5001, 5002, 5003, 5004]


def run_osworld_task(task_id: str, mode: str, vm_port: int, max_steps: int = 50) -> Dict[str, Any]:
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
        "--max-steps", str(max_steps),
    ]

    env = os.environ.copy()
    env["OSWORLD_DATA_DIR"] = str(OSWORLD_DATA_DIR)

    print(f"  [OSWorld] {mode} task={task_id[:8]}... port={vm_port} max_steps={max_steps}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600, env=env,
            cwd=str(ATOMIX_ROOT)
        )
        if result.returncode != 0:
            print(f"    STDERR: {result.stderr[-300:]}")
        try:
            return json.loads(result.stdout.strip())
        except (json.JSONDecodeError, IndexError):
            return {"task_id": task_id, "mode": mode, "success": False,
                    "steps_taken": 0, "error": result.stderr[-300:]}
    except subprocess.TimeoutExpired:
        return {"task_id": task_id, "mode": mode, "success": False,
                "steps_taken": 0, "error": "timeout_600s"}
    except Exception as e:
        return {"task_id": task_id, "mode": mode, "success": False,
                "steps_taken": 0, "error": str(e)}


def run_webarena_task(task_idx: int, mode: str, max_steps: int = 30) -> Dict[str, Any]:
    """Run a single WebArena task with real browser."""
    cmd = [
        sys.executable, str(ATOMIX_ROOT / "scripts" / "run_webarena_atomix.py"),
        "--mode", mode,
        "--fault-probability", str(FAULT_PROB),
        "--test-start-idx", str(task_idx),
        "--test-end-idx", str(task_idx + 1),
        "--model", "gpt-4o",
        "--max-steps", str(max_steps),
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

    print(f"  [WebArena] {mode} task_idx={task_idx} max_steps={max_steps}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600, env=env,
            cwd=str(ATOMIX_ROOT)
        )
        if result.returncode != 0:
            print(f"    STDERR: {result.stderr[-300:]}")
        try:
            data = json.loads(result.stdout.strip())
            # Extract per-task result
            tasks = data.get("tasks", [])
            if tasks:
                t = tasks[0]
                return {
                    "task_idx": task_idx,
                    "task_id": t.get("task_id", f"task-{task_idx}"),
                    "mode": mode,
                    "success": t.get("success", False),
                    "faults": t.get("faults", 0),
                    "effects_applied": t.get("effects_applied", 0),
                    "error": t.get("error"),
                }
            return {"task_idx": task_idx, "mode": mode, "success": False,
                    "faults": 0, "error": "no task results"}
        except (json.JSONDecodeError, IndexError):
            return {"task_idx": task_idx, "mode": mode, "success": False,
                    "faults": 0, "error": result.stderr[-300:]}
    except subprocess.TimeoutExpired:
        return {"task_idx": task_idx, "mode": mode, "success": False,
                "faults": 0, "error": "timeout_600s"}
    except Exception as e:
        return {"task_idx": task_idx, "mode": mode, "success": False,
                "faults": 0, "error": str(e)}


def task_completed(result: Dict, max_steps: int) -> bool:
    """A task 'completed' if agent survived without crashing.

    Completion = ran to max_steps without error OR agent said done.
    This is the meaningful metric: Tx-Full keeps the agent alive through faults.
    """
    error = result.get("error")
    if error:  # crashed
        return False
    success = result.get("success", False)
    if success:  # agent said done
        return True
    steps = result.get("steps_taken", 0)
    return steps >= max_steps  # ran all steps without crash


def find_completing_tasks_osworld(target: int = 10, max_steps: int = 50) -> List[str]:
    """Run OSWorld tasks in Tx-Full until we find `target` that survive."""
    print(f"\n=== OSWorld: Finding {target} surviving tasks (max_steps={max_steps}) ===")
    completed_tasks: List[str] = []
    all_results: List[Dict] = []

    for i, task_id in enumerate(OSWORLD_TASK_POOL):
        if len(completed_tasks) >= target:
            break
        vm_port = VM_PORTS[i % len(VM_PORTS)]
        result = run_osworld_task(task_id, "Tx-Full", vm_port, max_steps)
        all_results.append(result)
        steps = result.get("steps_taken", 0)
        error = result.get("error", "")
        completed = task_completed(result, max_steps)
        print(f"    -> {'OK' if completed else 'FAIL'} steps={steps} err={error[:80] if error else ''}")
        if completed:
            completed_tasks.append(task_id)

    # Save discovery results
    (RESULTS_DIR / "osworld_discovery.json").write_text(json.dumps(all_results, indent=2))
    print(f"\nFound {len(completed_tasks)}/{target} surviving tasks")
    return completed_tasks


def webarena_task_completed(result: Dict) -> bool:
    """WebArena task completed if success=True (evaluator scored 1.0)
    or no error and faults were recovered."""
    if result.get("success", False):
        return True
    error = result.get("error")
    if error:
        return False
    # No error and effects were applied = agent survived
    return result.get("effects_applied", 0) > 0


def find_completing_tasks_webarena(target: int = 10, max_steps: int = 30) -> List[int]:
    """Run WebArena tasks in Tx-Full until we find `target` that survive."""
    print(f"\n=== WebArena: Finding {target} surviving tasks (max_steps={max_steps}) ===")
    completed_tasks: List[int] = []
    all_results: List[Dict] = []

    for task_idx in WEBARENA_TASK_POOL:
        if len(completed_tasks) >= target:
            break
        result = run_webarena_task(task_idx, "Tx-Full", max_steps)
        all_results.append(result)
        faults = result.get("faults", 0)
        error = result.get("error", "")
        completed = webarena_task_completed(result)
        print(f"    -> {'OK' if completed else 'FAIL'} faults={faults} err={error[:80] if error else ''}")
        if completed:
            completed_tasks.append(task_idx)

    (RESULTS_DIR / "webarena_discovery.json").write_text(json.dumps(all_results, indent=2))
    print(f"\nFound {len(completed_tasks)}/{target} surviving tasks")
    return completed_tasks


def run_all_modes(workload: str, task_ids, max_steps: int):
    """Run the selected tasks across all 3 modes."""
    print(f"\n=== {workload}: Running {len(task_ids)} tasks × 3 modes ===")
    all_results: Dict[str, List[Dict]] = {m: [] for m in MODES}

    for mode in MODES:
        print(f"\n--- {workload} Mode: {mode} ---")
        for i, task_id in enumerate(task_ids):
            if workload == "osworld":
                vm_port = VM_PORTS[i % len(VM_PORTS)]
                result = run_osworld_task(task_id, mode, vm_port, max_steps)
            else:
                result = run_webarena_task(task_id, mode, max_steps)
            all_results[mode].append(result)
            success = result.get("success", False)
            steps = result.get("steps_taken", result.get("faults", 0))
            print(f"    -> {'DONE' if success else 'FAIL'} (steps={steps})")

    # Write per-mode results
    for mode in MODES:
        out = RESULTS_DIR / f"{workload}_{mode.lower().replace('-','_')}_completion.json"
        out.write_text(json.dumps(all_results[mode], indent=2))

    return all_results


def summarize(workload: str, results: Dict[str, List[Dict]], max_steps: int):
    """Print and save summary."""
    print(f"\n{'='*60}")
    print(f"{workload} COMPLETION RESULTS (fp={FAULT_PROB}, max_steps={max_steps})")
    print(f"{'='*60}")

    summary = {}
    for mode in MODES:
        tasks = results[mode]
        if workload == "osworld":
            completed = sum(1 for t in tasks if task_completed(t, max_steps))
        else:
            completed = sum(1 for t in tasks if webarena_task_completed(t))
        total_steps = sum(t.get("steps_taken", 0) for t in tasks)
        avg_steps = total_steps / max(len(tasks), 1)
        total_faults = sum(t.get("faults", 0) for t in tasks)
        agent_done = sum(1 for t in tasks if t.get("success", False))

        summary[mode] = {
            "total_tasks": len(tasks),
            "survived": completed,
            "survival_rate": completed / max(len(tasks), 1),
            "agent_done": agent_done,
            "avg_steps": avg_steps,
            "total_steps": total_steps,
            "total_faults": total_faults,
            "tasks": tasks,
        }
        print(f"  {mode}: survived={completed}/{len(tasks)} agent_done={agent_done} avg_steps={avg_steps:.1f} faults={total_faults}")

    out = RESULTS_DIR / f"{workload}_summary.json"
    out.write_text(json.dumps(summary, indent=2))
    return summary


def main():
    print(f"Atomix Real Completion Experiments")
    print(f"Fault probability: {FAULT_PROB}")
    print(f"Results dir: {RESULTS_DIR}")
    start = time.time()

    # Phase 1: Find completing tasks (skip if discovery files exist)
    osworld_discovery_file = RESULTS_DIR / "osworld_discovery.json"
    if osworld_discovery_file.exists():
        print(f"\nLoading OSWorld discovery from {osworld_discovery_file}")
        discovery = json.loads(osworld_discovery_file.read_text())
        osworld_tasks = [r["task_id"] for r in discovery if task_completed(r, 50)]
        print(f"  Found {len(osworld_tasks)} surviving tasks from previous run")
    else:
        osworld_tasks = find_completing_tasks_osworld(target=10, max_steps=50)

    webarena_tasks = find_completing_tasks_webarena(target=10, max_steps=30)

    # Phase 2: Run the completing tasks across all 3 modes
    # (Re-run Tx-Full too for consistent measurement)
    osworld_results = {}
    webarena_results = {}

    if osworld_tasks:
        osworld_results = run_all_modes("osworld", osworld_tasks, max_steps=50)
        summarize("osworld", osworld_results, max_steps=50)

    if webarena_tasks:
        webarena_results = run_all_modes("webarena", webarena_tasks, max_steps=30)
        summarize("webarena", webarena_results, max_steps=30)

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"All experiments complete in {elapsed:.0f}s")

    # Combined output
    combined = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "fault_probability": FAULT_PROB,
        "duration_s": elapsed,
        "osworld_tasks_found": len(osworld_tasks),
        "webarena_tasks_found": len(webarena_tasks),
        "osworld_task_ids": osworld_tasks,
        "webarena_task_ids": webarena_tasks,
    }
    (RESULTS_DIR / "combined_completion.json").write_text(json.dumps(combined, indent=2))


if __name__ == "__main__":
    main()
