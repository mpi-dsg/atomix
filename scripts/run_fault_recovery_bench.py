#!/usr/bin/env python3
"""E3.5 Fault Recovery Benchmark.

Simulates a multi-step task (N sequential tool calls building up state).
Injects faults at random points. Measures final state corruption,
partial effect leakage, and compensation success across modes.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Set

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atomix.adapters import ToolAdapter
from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.runtime import AtomixRuntime
from atomix.tool_result import ToolMeta, normalize_tool_result


class TaskState:
    """Represents the external state that a multi-step task builds up."""

    def __init__(self) -> None:
        self.resources: Dict[str, str] = {}
        self.history: List[str] = []

    def write(self, key: str, value: str) -> None:
        self.resources[key] = value
        self.history.append(f"write:{key}={value}")

    def delete(self, key: str) -> None:
        self.resources.pop(key, None)
        self.history.append(f"delete:{key}")

    def snapshot(self) -> Dict[str, str]:
        return dict(self.resources)


class StepAdapter(ToolAdapter):
    name = "task_step"

    def scopes(self, args: Dict[str, Any]) -> Set[str]:
        return {f"resource:{args['key']}"}

    def to_effect(self, args: Dict[str, Any], result: Any, epoch: Epoch) -> Effect:
        key = str(args["key"])
        value = str(args["value"])
        state: TaskState = args["_state"]

        def compensation() -> None:
            state.delete(key)

        return Effect(
            description=f"step:{key}={value}@{epoch.value}",
            scopes={f"resource:{key}"},
            payload={"key": key, "value": value},
            idempotency_key=f"{epoch.trace_id}:{epoch.value}:{key}",
            compensation=compensation,
        )


def _apply_step(state: TaskState, effect: Effect) -> None:
    payload = effect.payload if isinstance(effect.payload, dict) else {}
    state.write(str(payload["key"]), str(payload["value"]))


def _run_task(
    n_steps: int,
    fault_prob: float,
    mode: str,
    trace_id: str,
) -> Dict[str, Any]:
    """Run a single multi-step task and return metrics."""
    state = TaskState()
    runtime = AtomixRuntime(
        apply_effect=lambda eff: _apply_step(state, eff),
        effect_log_path=None,
        frontier_enabled=(mode == "Tx-Full"),
    )
    runtime.register_adapter("task_step", StepAdapter())

    expected_state: Dict[str, str] = {}
    steps_attempted = 0
    steps_succeeded = 0
    faults_injected = 0
    compensations_triggered = 0

    for step_idx in range(n_steps):
        key = f"r{step_idx}"
        value = f"v{step_idx}"
        expected_state[key] = value
        steps_attempted += 1

        # Determine if this step gets a fault
        inject_fault = random.random() < fault_prob

        if mode == "No-Tx":
            # Direct apply, no protection
            if inject_fault:
                faults_injected += 1
                # Fault after partial apply: state is corrupted
                state.write(key, f"CORRUPTED-{step_idx}")
            else:
                state.write(key, value)
                steps_succeeded += 1
        elif mode == "CR":
            # Checkpoint-Rollback: snapshot before, execute directly, rollback+retry on fault
            checkpoint = state.snapshot()
            if inject_fault:
                faults_injected += 1
                # Fault during execution: partial/corrupt write
                state.write(key, f"CORRUPTED-{step_idx}")
                # Rollback to checkpoint
                state.resources = dict(checkpoint)
                compensations_triggered += 1
                # Retry: execute cleanly
                state.write(key, value)
                steps_succeeded += 1
            else:
                state.write(key, value)
                steps_succeeded += 1
        else:
            # Tx-Full or No-Frontier: use runtime
            args = {"key": key, "value": value, "_state": state}
            epoch = runtime.epochs.next(trace_id=trace_id)

            if inject_fault:
                faults_injected += 1
                # Simulate fault: begin tx, record effect, then fail before commit
                adapter = runtime.adapters.get("task_step")
                scopes = adapter.scopes(args)
                tx = runtime.tx_manager.begin(scopes, epoch)
                meta = ToolMeta(
                    tool_name="task_step",
                    trace_id=epoch.trace_id,
                    branch_id=epoch.branch_id,
                    attempt=0,
                )
                tool_result = normalize_tool_result(args, meta=meta)
                effect = adapter.to_effect(args, tool_result, epoch)
                runtime.tx_manager.record_effect(tx, effect)
                # Fault: abort the transaction (simulates exception during tool call)
                runtime.tx_manager.abort(tx, "injected fault")
                compensations_triggered += sum(
                    1 for e in tx.effects if e.applied and e.compensation
                )
                # In Tx-Full, we retry after abort
                if mode == "Tx-Full":
                    # Retry: fresh epoch, should succeed
                    retry_epoch = runtime.epochs.next(trace_id=trace_id)
                    try:
                        runtime.run_tool(
                            "task_step", lambda **kw: kw, args, retry_epoch
                        )
                        steps_succeeded += 1
                    except Exception:
                        pass  # Retry also failed (shouldn't happen without fault)
                # No-Frontier: no retry, step is lost
            else:
                try:
                    runtime.run_tool("task_step", lambda **kw: kw, args, epoch)
                    steps_succeeded += 1
                except Exception:
                    pass

    final_state = state.snapshot()

    # Metrics
    corruption_count = 0
    missing_count = 0
    for key, expected_val in expected_state.items():
        actual = final_state.get(key)
        if actual is None:
            missing_count += 1
        elif actual != expected_val:
            corruption_count += 1

    extra_keys = set(final_state.keys()) - set(expected_state.keys())

    return {
        "mode": mode,
        "n_steps": n_steps,
        "fault_prob": fault_prob,
        "steps_attempted": steps_attempted,
        "steps_succeeded": steps_succeeded,
        "faults_injected": faults_injected,
        "compensations_triggered": compensations_triggered,
        "corruption_count": corruption_count,
        "missing_count": missing_count,
        "extra_keys": len(extra_keys),
        "state_correct": corruption_count == 0 and missing_count == 0 and len(extra_keys) == 0,
        "corruption_rate": corruption_count / n_steps if n_steps > 0 else 0.0,
        "completion_rate": steps_succeeded / n_steps if n_steps > 0 else 0.0,
    }


def run(config_path: Path, output: Path, mode: str | None) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if mode:
        config["mode"] = mode
    mode_value = str(config.get("mode", "Tx-Full"))

    variants = config.get("variants", {})
    n_steps = int(variants.get("n_steps", 10))
    fault_probs = variants.get("fault_probabilities", [0.0, 0.1, 0.3, 0.5])
    runs_per = int(variants.get("runs_per_config", 50))

    output.parent.mkdir(parents=True, exist_ok=True)
    records: List[Dict[str, Any]] = []

    for fault_prob in fault_probs:
        for run_idx in range(runs_per):
            trace_id = f"{config['trace_id']}-{fault_prob}-{run_idx}"
            result = _run_task(n_steps, fault_prob, mode_value, trace_id)
            result["experiment"] = config.get("experiment", "E3.5")
            result["trace_id"] = trace_id
            result["run_idx"] = run_idx
            records.append(result)

    output.write_text(json.dumps(records, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", help="Mode override")
    args = parser.parse_args()
    run(Path(args.config), Path(args.output), args.mode)
    print(f"Wrote fault recovery results to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
