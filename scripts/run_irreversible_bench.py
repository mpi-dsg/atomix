#!/usr/bin/env python3
"""E3.7 Irreversible Effect Gate Benchmark.

Tests that Atomix correctly gates irreversible effects (emails, payments)
by requiring explicit confirmation before commit. Measures:
- How many irreversible effects are correctly blocked without confirmation
- How many irreversible effects fire without a gate (baselines)
- False fires: irreversible effects applied on branches that later abort
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Set

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atomix.adapters import ToolAdapter
from atomix.effects import Effect, EffectReversibility
from atomix.epoch import Epoch
from atomix.runtime import AtomixRuntime
from atomix.tool_result import ToolMeta, normalize_tool_result
from atomix.transactions import IrreversibleEffectError


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

class ExternalState:
    """Mock external state tracking reversible and irreversible effects."""

    def __init__(self) -> None:
        self.files: Dict[str, str] = {}
        self.sent_emails: List[Dict[str, str]] = []  # cannot unsend
        self.history: List[str] = []

    def write_file(self, path: str, content: str) -> None:
        self.files[path] = content
        self.history.append(f"file_write:{path}")

    def delete_file(self, path: str) -> None:
        self.files.pop(path, None)
        self.history.append(f"file_delete:{path}")

    def send_email(self, to: str, subject: str, body: str) -> None:
        self.sent_emails.append({"to": to, "subject": subject, "body": body})
        self.history.append(f"email:{to}:{subject}")

    def snapshot(self) -> Dict[str, Any]:
        return {
            "files": dict(self.files),
            "sent_emails": list(self.sent_emails),
        }


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

class FileWriteAdapter(ToolAdapter):
    name = "file_write"

    def scopes(self, args: Dict[str, Any]) -> Set[str]:
        return {f"file:{args['path']}"}

    def to_effect(self, args: Dict[str, Any], result: Any, epoch: Epoch) -> Effect:
        path = str(args["path"])
        content = str(args["content"])
        state: ExternalState = args["_state"]

        def compensation() -> None:
            state.delete_file(path)

        return Effect(
            description=f"file_write:{path}",
            scopes={f"file:{path}"},
            payload={"path": path, "content": content},
            idempotency_key=f"{epoch.trace_id}:{epoch.value}:file:{path}",
            compensation=compensation,
            reversibility=EffectReversibility.REVERSIBLE,
        )


class SendEmailAdapter(ToolAdapter):
    name = "send_email"

    def scopes(self, args: Dict[str, Any]) -> Set[str]:
        return {f"email:{args['to']}"}

    def to_effect(self, args: Dict[str, Any], result: Any, epoch: Epoch) -> Effect:
        to = str(args["to"])
        subject = str(args["subject"])
        state: ExternalState = args["_state"]

        return Effect(
            description=f"send_email:{to}:{subject}",
            scopes={f"email:{to}"},
            payload={"to": to, "subject": subject, "body": args.get("body", "")},
            idempotency_key=f"{epoch.trace_id}:{epoch.value}:email:{to}:{subject}",
            compensation=None,  # Cannot unsend
            reversibility=EffectReversibility.IRREVERSIBLE,
            confirmed=False,  # Requires explicit confirmation
        )


# ---------------------------------------------------------------------------
# Effect application
# ---------------------------------------------------------------------------

def _apply_effect(state: ExternalState, effect: Effect) -> None:
    payload = effect.payload if isinstance(effect.payload, dict) else {}
    if effect.description.startswith("file_write:"):
        state.write_file(str(payload["path"]), str(payload["content"]))
    elif effect.description.startswith("send_email:"):
        state.send_email(
            str(payload["to"]), str(payload["subject"]), str(payload.get("body", ""))
        )


# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------

def _make_task_steps(n_steps: int, n_irreversible: int) -> List[Dict[str, Any]]:
    """Generate a mixed task: mostly file writes with some email sends.

    Irreversible steps are placed at fixed positions to ensure consistent
    measurement. Positions: evenly spaced starting from step n_steps//3.
    """
    steps: List[Dict[str, Any]] = []
    irrev_positions: Set[int] = set()

    if n_irreversible > 0:
        start = n_steps // 3
        spacing = max(1, (n_steps - start) // n_irreversible)
        for i in range(n_irreversible):
            pos = min(start + i * spacing, n_steps - 1)
            irrev_positions.add(pos)

    for i in range(n_steps):
        if i in irrev_positions:
            steps.append({
                "tool": "send_email",
                "args": {
                    "to": f"user{i}@example.com",
                    "subject": f"Notification step {i}",
                    "body": f"Task step {i} completed",
                },
            })
        else:
            steps.append({
                "tool": "file_write",
                "args": {
                    "path": f"/tmp/task/step_{i}.txt",
                    "content": f"data-{i}",
                },
            })
    return steps


# ---------------------------------------------------------------------------
# Single run
# ---------------------------------------------------------------------------

def _run_task(
    n_steps: int,
    n_irreversible: int,
    fault_prob: float,
    mode: str,
    trace_id: str,
    auto_confirm: bool,
) -> Dict[str, Any]:
    """Run one task and return metrics."""

    state = ExternalState()
    runtime = AtomixRuntime(
        apply_effect=lambda eff: _apply_effect(state, eff),
        effect_log_path=None,
        frontier_enabled=(mode == "Tx-Full"),
    )
    runtime.register_adapter("file_write", FileWriteAdapter())
    runtime.register_adapter("send_email", SendEmailAdapter())

    steps = _make_task_steps(n_steps, n_irreversible)

    metrics = {
        "steps_attempted": 0,
        "steps_succeeded": 0,
        "faults_injected": 0,
        "irreversible_gated": 0,       # blocked by confirmation gate
        "irreversible_confirmed": 0,    # confirmed and committed
        "irreversible_leaked": 0,       # applied without gate (baselines)
        "irreversible_false_fire": 0,   # applied then aborted (worst case)
        "compensations_triggered": 0,
    }

    for step_idx, step in enumerate(steps):
        tool_name = step["tool"]
        args = {**step["args"], "_state": state}
        metrics["steps_attempted"] += 1

        inject_fault = random.random() < fault_prob

        if mode == "No-Tx":
            # Direct apply, no protection
            if inject_fault:
                metrics["faults_injected"] += 1
                if tool_name == "send_email":
                    # Email fires anyway (no gate)
                    _apply_effect(state, SendEmailAdapter().to_effect(
                        args, args, Epoch(step_idx, trace_id)
                    ))
                    metrics["irreversible_leaked"] += 1
                    metrics["irreversible_false_fire"] += 1
                # File writes also corrupted on fault in No-Tx
            else:
                if tool_name == "send_email":
                    state.send_email(args["to"], args["subject"], args.get("body", ""))
                    metrics["irreversible_leaked"] += 1
                else:
                    state.write_file(args["path"], args["content"])
                metrics["steps_succeeded"] += 1
        elif mode == "CR":
            # Checkpoint-Rollback: snapshot before, execute directly, rollback+retry on fault
            # Irreversible effects (emails) fire immediately — no gate
            checkpoint_files = dict(state.files)
            checkpoint_emails = list(state.sent_emails)

            if inject_fault:
                metrics["faults_injected"] += 1
                if tool_name == "send_email":
                    # Email fires immediately — cannot unsend
                    state.send_email(args["to"], args["subject"], args.get("body", ""))
                    metrics["irreversible_leaked"] += 1
                    metrics["irreversible_false_fire"] += 1
                else:
                    state.write_file(args["path"], f"CORRUPTED-{step_idx}")
                # Rollback reversible state (files), but emails are permanent
                state.files = dict(checkpoint_files)
                # Emails stay — they're irreversible
                metrics["compensations_triggered"] += 1
                # Retry
                if tool_name == "send_email":
                    # Email already sent during fault — duplicate on retry
                    state.send_email(args["to"], args["subject"], args.get("body", ""))
                    metrics["irreversible_leaked"] += 1
                else:
                    state.write_file(args["path"], args["content"])
                metrics["steps_succeeded"] += 1
            else:
                if tool_name == "send_email":
                    state.send_email(args["to"], args["subject"], args.get("body", ""))
                    metrics["irreversible_leaked"] += 1
                else:
                    state.write_file(args["path"], args["content"])
                metrics["steps_succeeded"] += 1
        else:
            # Tx-Full or No-Frontier: use runtime
            epoch = runtime.epochs.next(trace_id=trace_id)
            adapter = runtime.adapters.get(tool_name)
            scopes = adapter.scopes(args)
            tx = runtime.tx_manager.begin(scopes, epoch)

            if inject_fault:
                metrics["faults_injected"] += 1
                # Simulate fault: record effect then abort
                meta = ToolMeta(
                    tool_name=tool_name,
                    trace_id=epoch.trace_id,
                    branch_id=epoch.branch_id,
                    attempt=0,
                )
                tool_result = normalize_tool_result(args, meta=meta)
                effect = adapter.to_effect(args, tool_result, epoch)
                runtime.tx_manager.record_effect(tx, effect)
                runtime.tx_manager.abort(tx, "injected fault")

                metrics["compensations_triggered"] += sum(
                    1 for e in tx.effects if e.applied and e.compensation
                )

                if mode == "Tx-Full":
                    # Retry
                    retry_epoch = runtime.epochs.next(trace_id=trace_id)
                    retry_tx = runtime.tx_manager.begin(scopes, retry_epoch)
                    meta_retry = ToolMeta(
                        tool_name=tool_name,
                        trace_id=retry_epoch.trace_id,
                        branch_id=retry_epoch.branch_id,
                        attempt=1,
                    )
                    retry_result = normalize_tool_result(args, meta=meta_retry)
                    retry_effect = adapter.to_effect(args, retry_result, retry_epoch)
                    runtime.tx_manager.record_effect(retry_tx, retry_effect)

                    # For irreversible effects, confirm on retry if auto_confirm
                    if retry_effect.reversibility == EffectReversibility.IRREVERSIBLE:
                        if auto_confirm:
                            retry_effect.confirmed = True
                            metrics["irreversible_confirmed"] += 1

                    runtime.advance_frontier(scopes, retry_epoch)
                    try:
                        runtime.tx_manager.commit(retry_tx)
                        metrics["steps_succeeded"] += 1
                    except IrreversibleEffectError:
                        metrics["irreversible_gated"] += 1
                        runtime.tx_manager.abort(retry_tx, "unconfirmed irreversible")
            else:
                # No fault
                meta = ToolMeta(
                    tool_name=tool_name,
                    trace_id=epoch.trace_id,
                    branch_id=epoch.branch_id,
                    attempt=0,
                )
                tool_result = normalize_tool_result(args, meta=meta)
                effect = adapter.to_effect(args, tool_result, epoch)
                runtime.tx_manager.record_effect(tx, effect)

                # For irreversible effects, confirm if auto_confirm
                if effect.reversibility == EffectReversibility.IRREVERSIBLE:
                    if auto_confirm:
                        effect.confirmed = True
                        metrics["irreversible_confirmed"] += 1

                runtime.advance_frontier(scopes, epoch)
                try:
                    runtime.tx_manager.commit(tx)
                    metrics["steps_succeeded"] += 1
                except IrreversibleEffectError:
                    metrics["irreversible_gated"] += 1
                    runtime.tx_manager.abort(tx, "unconfirmed irreversible")


    final_state = state.snapshot()

    return {
        "mode": mode,
        "n_steps": n_steps,
        "n_irreversible": n_irreversible,
        "fault_prob": fault_prob,
        "auto_confirm": auto_confirm,
        "final_files": len(final_state["files"]),
        "final_emails": len(final_state["sent_emails"]),
        **metrics,
    }


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------

def run(config_path: Path, output: Path, mode: str | None) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if mode:
        config["mode"] = mode
    mode_value = str(config.get("mode", "Tx-Full"))

    variants = config.get("variants", {})
    n_steps = int(variants.get("n_steps", 10))
    n_irreversible = int(variants.get("n_irreversible", 2))
    fault_probs = variants.get("fault_probabilities", [0.0, 0.3])
    runs_per = int(variants.get("runs_per_config", 100))

    output.parent.mkdir(parents=True, exist_ok=True)
    records: List[Dict[str, Any]] = []

    for fault_prob in fault_probs:
        for auto_confirm in [True, False]:
            for run_idx in range(runs_per):
                trace_id = f"{config['trace_id']}-fp{fault_prob}-c{auto_confirm}-{run_idx}"
                result = _run_task(
                    n_steps, n_irreversible, fault_prob,
                    mode_value, trace_id, auto_confirm,
                )
                result["experiment"] = config.get("experiment", "E3.7")
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
    print(f"Wrote irreversible gate results to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
