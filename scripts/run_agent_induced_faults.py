#!/usr/bin/env python3
"""Agent-Induced Fault Experiments.

Tests how Atomix handles faults that originate from agent behavior,
not from tool-boundary failures. Three fault types:

1. Invalid tool arguments: adapter rejects the call (bad params).
2. Hallucinated tool names: tool name does not exist, rejected before tx.
3. Constraint violations: valid call that violates a domain rule (e.g., overbooking).

No LLM calls. Synthetic workload with configurable fault injection.

Usage:
    python run_agent_induced_faults.py --runs 100
    python run_agent_induced_faults.py --runs 100 --output results/agent_faults.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Simulated environment
# ---------------------------------------------------------------------------

VALID_TOOLS = {"write_file", "read_file", "send_email", "query_db", "book_seat"}
VALID_PARAMS = {
    "write_file": {"path", "content"},
    "read_file": {"path"},
    "send_email": {"to", "subject", "body"},
    "query_db": {"query"},
    "book_seat": {"flight_id", "seat_id"},
}


@dataclass
class Environment:
    """Tracks state for correctness checking."""
    files: Dict[str, str] = field(default_factory=dict)
    emails_sent: List[Dict] = field(default_factory=list)
    seats_booked: Dict[str, str] = field(default_factory=dict)  # seat_id -> flight_id
    available_seats: int = 10
    compensations_run: int = 0
    effects_applied: int = 0
    effects_rejected: int = 0
    effects_retried: int = 0


class ToolCallError(Exception):
    """Raised when a tool call fails at the boundary."""
    pass


class InvalidArgsError(ToolCallError):
    """Adapter rejects: invalid or missing parameters."""
    pass


class UnknownToolError(ToolCallError):
    """Tool name does not exist."""
    pass


class ConstraintViolationError(ToolCallError):
    """Valid call that violates a domain constraint."""
    pass


# ---------------------------------------------------------------------------
# Simulated adapter + tool execution
# ---------------------------------------------------------------------------

def validate_tool_call(tool_name: str, args: Dict[str, Any]) -> None:
    """Simulate adapter validation. Raises before any transaction begins."""
    if tool_name not in VALID_TOOLS:
        raise UnknownToolError(f"Unknown tool: {tool_name}")

    required = VALID_PARAMS.get(tool_name, set())
    provided = set(args.keys())
    missing = required - provided
    if missing:
        raise InvalidArgsError(f"Missing params for {tool_name}: {missing}")

    # Type checks
    if tool_name == "write_file" and not isinstance(args.get("path"), str):
        raise InvalidArgsError(f"write_file: 'path' must be string, got {type(args.get('path'))}")


def execute_tool(env: Environment, tool_name: str, args: Dict[str, Any]) -> Any:
    """Execute tool and return result. May raise ConstraintViolationError."""
    if tool_name == "write_file":
        env.files[args["path"]] = args["content"]
        return {"status": "ok", "path": args["path"]}

    elif tool_name == "read_file":
        content = env.files.get(args["path"])
        if content is None:
            raise ConstraintViolationError(f"File not found: {args['path']}")
        return {"status": "ok", "content": content}

    elif tool_name == "send_email":
        env.emails_sent.append(args)
        return {"status": "sent"}

    elif tool_name == "book_seat":
        seat = args["seat_id"]
        if seat in env.seats_booked:
            raise ConstraintViolationError(f"Seat {seat} already booked")
        if env.available_seats <= 0:
            raise ConstraintViolationError("No seats available (overbooking)")
        env.seats_booked[seat] = args["flight_id"]
        env.available_seats -= 1
        return {"status": "booked", "seat": seat}

    return {"status": "ok"}


def compensate_tool(env: Environment, tool_name: str, args: Dict[str, Any]) -> None:
    """Undo the effect of a tool call."""
    if tool_name == "write_file":
        env.files.pop(args.get("path", ""), None)
    elif tool_name == "book_seat":
        seat = args.get("seat_id", "")
        if seat in env.seats_booked:
            del env.seats_booked[seat]
            env.available_seats += 1
    elif tool_name == "send_email":
        if args in env.emails_sent:
            env.emails_sent.remove(args)
    env.compensations_run += 1


# ---------------------------------------------------------------------------
# Experiment: run a task sequence with injected agent faults
# ---------------------------------------------------------------------------

@dataclass
class FaultConfig:
    invalid_args_prob: float = 0.0
    hallucinated_tool_prob: float = 0.0
    constraint_violation_prob: float = 0.0


@dataclass
class RunResult:
    correct_final_state: bool
    total_steps: int
    completed_steps: int
    faults_injected: int
    faults_by_type: Dict[str, int] = field(default_factory=dict)
    retries: int = 0
    compensations: int = 0
    unrecoverable: int = 0


HALLUCINATED_TOOLS = ["delete_universe", "hack_mainframe", "travel_time", "make_coffee"]


def inject_agent_fault(
    tool_name: str, args: Dict[str, Any], fault_config: FaultConfig
) -> tuple[str, Dict[str, Any], Optional[str]]:
    """Possibly corrupt the tool call to simulate an agent-induced fault.
    Returns (tool_name, args, fault_type_or_None)."""

    r = random.random()

    if r < fault_config.hallucinated_tool_prob:
        return random.choice(HALLUCINATED_TOOLS), args, "hallucinated_tool"

    r2 = random.random()
    if r2 < fault_config.invalid_args_prob:
        # Remove a required parameter
        corrupted = dict(args)
        if corrupted:
            key = random.choice(list(corrupted.keys()))
            del corrupted[key]
        return tool_name, corrupted, "invalid_args"

    r3 = random.random()
    if r3 < fault_config.constraint_violation_prob and tool_name == "book_seat":
        # Try to book an already-booked seat
        corrupted = dict(args)
        corrupted["seat_id"] = "seat_0"  # likely already booked
        return tool_name, corrupted, "constraint_violation"

    return tool_name, args, None


def run_task_sequence(
    mode: str,
    n_steps: int,
    fault_config: FaultConfig,
    max_retries: int = 3,
) -> RunResult:
    """Run a synthetic n-step task with agent-induced faults.

    Modes:
    - Tx-Full: wrap in transaction, retry on failure, compensate on abort
    - No-Tx: execute directly, faults propagate as errors
    """
    env = Environment()
    faults_injected = 0
    faults_by_type: Dict[str, int] = {"hallucinated_tool": 0, "invalid_args": 0, "constraint_violation": 0}
    retries = 0
    unrecoverable = 0
    completed = 0

    # Task: write files, book seats, send confirmation
    task_steps = []
    for i in range(n_steps):
        step_type = i % 3
        if step_type == 0:
            task_steps.append(("write_file", {"path": f"/tmp/file_{i}.txt", "content": f"data_{i}"}))
        elif step_type == 1:
            task_steps.append(("book_seat", {"flight_id": "FL100", "seat_id": f"seat_{i}"}))
        else:
            task_steps.append(("send_email", {"to": "user@test.com", "subject": f"Step {i}", "body": f"Done {i}"}))

    for step_idx, (tool_name, args) in enumerate(task_steps):
        success = False
        attempts = 0

        while not success and attempts <= max_retries:
            # Inject agent fault (only on first attempt; retries use clean call)
            if attempts == 0:
                actual_tool, actual_args, fault_type = inject_agent_fault(tool_name, args, fault_config)
            else:
                actual_tool, actual_args, fault_type = tool_name, args, None

            if fault_type:
                faults_injected += 1
                faults_by_type[fault_type] += 1

            try:
                # Phase 1: Validate (adapter check, before transaction)
                validate_tool_call(actual_tool, actual_args)

                # Phase 2: Execute (inside transaction)
                result = execute_tool(env, actual_tool, actual_args)
                env.effects_applied += 1
                success = True
                completed += 1

            except UnknownToolError:
                # Rejected before transaction. No effect to manage.
                env.effects_rejected += 1
                if mode == "Tx-Full":
                    attempts += 1
                    retries += 1
                    continue  # retry with clean call
                else:
                    unrecoverable += 1
                    break  # No-Tx: error propagates

            except InvalidArgsError:
                # Adapter rejects. No effect produced.
                env.effects_rejected += 1
                if mode == "Tx-Full":
                    attempts += 1
                    retries += 1
                    continue
                else:
                    unrecoverable += 1
                    break

            except ConstraintViolationError:
                # Tool detected violation. Effect may or may not have been applied.
                # In Tx-Full: compensate and retry
                env.effects_rejected += 1
                if mode == "Tx-Full":
                    compensate_tool(env, actual_tool, actual_args)
                    attempts += 1
                    retries += 1
                    continue
                else:
                    unrecoverable += 1
                    break

        if not success and mode == "Tx-Full":
            # Exhausted retries
            unrecoverable += 1

    # Check correctness: did we complete all steps?
    expected_files = sum(1 for i in range(n_steps) if i % 3 == 0)
    expected_seats = sum(1 for i in range(n_steps) if i % 3 == 1)
    expected_emails = sum(1 for i in range(n_steps) if i % 3 == 2)

    actual_files = len(env.files)
    actual_seats = len(env.seats_booked)
    actual_emails = len(env.emails_sent)

    correct = (completed == n_steps) and (unrecoverable == 0)

    return RunResult(
        correct_final_state=correct,
        total_steps=n_steps,
        completed_steps=completed,
        faults_injected=faults_injected,
        faults_by_type=faults_by_type,
        retries=retries,
        compensations=env.compensations_run,
        unrecoverable=unrecoverable,
    )


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_experiment(
    mode: str,
    n_steps: int,
    fault_config: FaultConfig,
    n_runs: int,
    max_retries: int = 3,
) -> Dict[str, Any]:
    results = []
    for _ in range(n_runs):
        r = run_task_sequence(mode, n_steps, fault_config, max_retries)
        results.append(r)

    correct_count = sum(1 for r in results if r.correct_final_state)
    total_faults = sum(r.faults_injected for r in results)
    total_retries = sum(r.retries for r in results)
    total_compensations = sum(r.compensations for r in results)
    total_unrecoverable = sum(r.unrecoverable for r in results)
    avg_completed = sum(r.completed_steps for r in results) / n_runs

    faults_agg = {}
    for ft in ["hallucinated_tool", "invalid_args", "constraint_violation"]:
        faults_agg[ft] = sum(r.faults_by_type.get(ft, 0) for r in results)

    return {
        "mode": mode,
        "n_runs": n_runs,
        "n_steps": n_steps,
        "correctness_rate": round(correct_count / n_runs, 4),
        "avg_completed_steps": round(avg_completed, 2),
        "total_faults_injected": total_faults,
        "faults_by_type": faults_agg,
        "total_retries": total_retries,
        "total_compensations": total_compensations,
        "total_unrecoverable": total_unrecoverable,
    }


def main():
    parser = argparse.ArgumentParser(description="Agent-Induced Fault Experiments")
    parser.add_argument("--runs", type=int, default=100, help="Runs per configuration")
    parser.add_argument("--steps", type=int, default=15, help="Steps per task")
    parser.add_argument("--output", type=str, help="Output JSON file")
    args = parser.parse_args()

    print("Agent-Induced Fault Experiments")
    print(f"Runs: {args.runs}, Steps per task: {args.steps}")
    print()

    modes = ["Tx-Full", "No-Tx"]

    # Experiment 1: Invalid arguments only
    # Experiment 2: Hallucinated tools only
    # Experiment 3: Constraint violations only
    # Experiment 4: Mixed (all three)
    configs = {
        "invalid_args_only": FaultConfig(invalid_args_prob=0.2),
        "hallucinated_tools_only": FaultConfig(hallucinated_tool_prob=0.2),
        "constraint_violations_only": FaultConfig(constraint_violation_prob=0.3),
        "mixed_agent_faults": FaultConfig(
            invalid_args_prob=0.1,
            hallucinated_tool_prob=0.1,
            constraint_violation_prob=0.1,
        ),
        "no_faults": FaultConfig(),  # baseline
    }

    all_results = {}
    start = time.time()

    for config_name, fault_config in configs.items():
        print(f"=== {config_name} ===")
        all_results[config_name] = {}
        for mode in modes:
            print(f"  {mode}...", end=" ", flush=True)
            result = run_experiment(mode, args.steps, fault_config, args.runs)
            all_results[config_name][mode] = result
            print(f"correct={result['correctness_rate']*100:.0f}%  "
                  f"faults={result['total_faults_injected']}  "
                  f"retries={result['total_retries']}  "
                  f"unrecoverable={result['total_unrecoverable']}")
        print()

    elapsed = time.time() - start

    # Summary table
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"{'Config':<30} {'Mode':<10} {'Correct%':>10} {'Faults':>8} {'Retries':>8} {'Unrecover':>10}")
    print("-" * 80)
    for config_name in configs:
        for mode in modes:
            r = all_results[config_name][mode]
            print(f"{config_name:<30} {mode:<10} {r['correctness_rate']*100:>9.1f}% "
                  f"{r['total_faults_injected']:>8} {r['total_retries']:>8} "
                  f"{r['total_unrecoverable']:>10}")

    output_data = {
        "results": all_results,
        "metadata": {
            "runs_per_config": args.runs,
            "steps_per_task": args.steps,
            "modes": modes,
            "duration_s": round(elapsed, 2),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    }

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(output_data, indent=2))
        print(f"\nResults saved to {args.output}")

    print(f"\nCompleted in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
