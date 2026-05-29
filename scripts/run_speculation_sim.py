#!/usr/bin/env python3
"""Synthetic speculation run (E2/E3.3) with observer polling."""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atomix.adapters import ToolAdapter
from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.runtime import AtomixRuntime
from atomix.tool_result import ToolMeta, normalize_tool_result


class ObjectStore:
    def __init__(self) -> None:
        self.objects: Dict[str, str] = {}

    def create(self, object_id: str, value: str) -> None:
        self.objects[object_id] = value

    def delete(self, object_id: str) -> None:
        self.objects.pop(object_id, None)

    def snapshot(self) -> Dict[str, str]:
        return dict(self.objects)


class ObjectCreateAdapter(ToolAdapter):
    name = "object_create"

    def scopes(self, args: dict[str, Any]) -> set[str]:
        return {f"obj:{args['object_id']}"}

    def to_effect(self, args: dict[str, Any], result: Any, epoch: Epoch) -> Effect:
        object_id = str(args["object_id"])
        value = str(args["value"])

        def compensation() -> None:
            args["store"].delete(object_id)

        return Effect(
            description=f"object:{object_id}@{epoch.value}",
            scopes={f"obj:{object_id}"},
            payload={"object_id": object_id, "value": value},
            idempotency_key=f"obj:{object_id}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
            compensation=compensation,
        )


def _apply_object_effect(store: ObjectStore, effect: Effect) -> None:
    payload = effect.payload if isinstance(effect.payload, dict) else {}
    store.create(str(payload["object_id"]), str(payload["value"]))


def _poll_observer(
    store: ObjectStore, poll_ms: int, duration_s: float
) -> List[Dict[str, Any]]:
    snapshots: List[Dict[str, Any]] = []
    start = time.perf_counter()
    while time.perf_counter() - start < duration_s:
        snapshots.append({"timestamp": time.time(), "state": store.snapshot()})
        time.sleep(poll_ms / 1000.0)
    return snapshots


def run(config_path: Path, output: Path, mode: str | None) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if mode:
        config["mode"] = mode
    variants = config.get("variants", {})
    k_list = variants.get("k_branches", [2])
    runs_per = int(variants.get("runs_per_config", 1))
    poll_ms = int(config.get("observer", {}).get("poll_ms", 100))
    workload = str(config.get("workload", "speculative_discard"))
    mode_value = str(config.get("mode", "Tx-Full"))

    output.parent.mkdir(parents=True, exist_ok=True)
    records: List[Dict[str, Any]] = []

    for k in k_list:
        for run_idx in range(runs_per):
            store = ObjectStore()
            runtime = AtomixRuntime(
                apply_effect=lambda eff: _apply_object_effect(store, eff),
                frontier_enabled=mode_value != "No-Frontier",
            )
            runtime.register_adapter("object_create", ObjectCreateAdapter())
            trace_id = f"{config['trace_id']}-{k}-{run_idx}"
            branch_ids = [f"b{idx}" for idx in range(k)]

            observer_duration = 1.0
            snapshots = []

            branch_epochs: Dict[str, Epoch] = {}
            branch_args: Dict[str, Dict[str, Any]] = {}
            branch_txs: Dict[str, Any] = {}

            # CR mode: checkpoint before any branch execution
            cr_checkpoint = store.snapshot() if mode_value == "CR" else None

            for branch in branch_ids:
                epoch = runtime.epochs.next(trace_id=trace_id, branch_id=branch)
                object_id = f"{branch}-{run_idx}"
                args = {
                    "object_id": object_id,
                    "value": f"value-{branch}",
                    "store": store,
                }
                if mode_value in ("No-Tx", "CR") or workload == "speculation":
                    # CR and No-Tx both apply effects immediately (visible to observers)
                    _apply_object_effect(
                        store, ObjectCreateAdapter().to_effect(args, args, epoch)
                    )
                    branch_epochs[branch] = epoch
                    branch_args[branch] = args
                else:
                    if mode_value == "Tx-Full":
                        adapter = runtime.adapters.get("object_create")
                        scopes = adapter.scopes(args)
                        tx = runtime.tx_manager.begin(scopes, epoch)
                        meta = ToolMeta(
                            tool_name="object_create",
                            trace_id=epoch.trace_id,
                            branch_id=epoch.branch_id,
                            attempt=0,
                        )
                        tool_result = normalize_tool_result(args, meta=meta)
                        effect = adapter.to_effect(args, tool_result, epoch)
                        runtime.tx_manager.record_effect(tx, effect)
                        branch_txs[branch] = tx
                        branch_epochs[branch] = epoch
                        branch_args[branch] = args
                    else:
                        runtime.run_tool(
                            "object_create", lambda **kwargs: kwargs, args, epoch
                        )
                        branch_epochs[branch] = epoch
                        branch_args[branch] = args
                        runtime.advance_frontier(
                            runtime.adapters.get("object_create").scopes(args),
                            epoch,
                        )
                snapshots.extend(_poll_observer(store, poll_ms, observer_duration))

            winner = random.choice(branch_ids)
            if mode_value == "CR":
                # CR: rollback to checkpoint, replay only the winner
                store.objects = dict(cr_checkpoint)
                winner_id = f"{winner}-{run_idx}"
                store.create(winner_id, f"value-{winner}")
            elif mode_value == "Tx-Full" and winner in branch_txs:
                epoch = branch_epochs[winner]
                scopes = runtime.adapters.get("object_create").scopes(
                    branch_args[winner]
                )
                runtime.advance_frontier(scopes, epoch)
                runtime.tx_manager.commit(branch_txs[winner])
            for branch in branch_ids:
                if branch != winner:
                    if mode_value not in ("No-Tx", "CR"):
                        if mode_value == "Tx-Full" and branch in branch_txs:
                            runtime.tx_manager.abort(branch_txs[branch], "loser branch")
                        else:
                            runtime.abort_branch(branch)
                    if mode_value != "CR":  # CR already rolled back
                        store.delete(f"{branch}-{run_idx}")

            final_state = store.snapshot()
            transient_visible = any(
                f"{branch}-{run_idx}" in snap["state"]
                for branch in branch_ids
                for snap in snapshots
                if branch != winner
            )
            end_state_contamination = any(
                f"{branch}-{run_idx}" in final_state
                for branch in branch_ids
                if branch != winner
            )

            records.append(
                {
                    "experiment": config.get("experiment", "E2"),
                    "trace_id": trace_id,
                    "mode": mode_value,
                    "k_branches": k,
                    "winner": winner,
                    "transient_contamination": transient_visible,
                    "end_state_contamination": end_state_contamination,
                    "snapshots": snapshots,
                    "final_state": final_state,
                }
            )

    output.write_text(json.dumps(records, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Experiment config JSON")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--mode", help="Optional mode override")
    args = parser.parse_args()

    run(Path(args.config), Path(args.output), args.mode)
    print(f"Wrote speculation results to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
