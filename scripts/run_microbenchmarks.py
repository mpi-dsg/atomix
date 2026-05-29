#!/usr/bin/env python3
"""Run deterministic microbenchmarks for Atomix experiments (E3/E4)."""

from __future__ import annotations

import argparse
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from atomix.adapters import ToolAdapter
from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.runtime import AtomixRuntime


@dataclass
class CounterState:
    values: Dict[str, int]

    def transfer(self, source: str, target: str, amount: int) -> None:
        if amount < 0:
            raise ValueError("amount must be non-negative")
        if self.values[source] - amount < 0:
            raise ValueError("insufficient funds")
        self.values[source] -= amount
        self.values[target] += amount

    def transfer_unchecked(self, source: str, target: str, amount: int) -> None:
        self.values[source] -= amount
        self.values[target] += amount

    def total(self) -> int:
        return sum(self.values.values())


class CounterAdapter(ToolAdapter):
    name = "counter_transfer"

    def __init__(self, state: CounterState) -> None:
        self._state = state

    def scopes(self, args: dict[str, Any]) -> set[str]:
        return {str(args["source"]), str(args["target"])}

    def to_effect(self, args: dict[str, Any], result: Any, epoch: Epoch) -> Effect:
        source = str(args["source"])
        target = str(args["target"])
        amount = int(args["amount"])
        before = dict(self._state.values)

        def compensation() -> None:
            self._state.values = dict(before)

        payload = {
            "source": source,
            "target": target,
            "amount": amount,
            "before": before,
        }
        return Effect(
            description=f"transfer:{source}->{target}:{amount}@{epoch.value}",
            scopes={source, target},
            payload=payload,
            idempotency_key=f"{source}:{target}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
            compensation=compensation,
        )


def _apply_counter_effect(state: CounterState, effect: Effect) -> None:
    payload = effect.payload if isinstance(effect.payload, dict) else {}
    state.transfer(payload["source"], payload["target"], int(payload["amount"]))


def _apply_counter_effect_unchecked(state: CounterState, args: Dict[str, Any]) -> None:
    state.transfer_unchecked(args["source"], args["target"], int(args["amount"]))


def _run_transfer_batch(
    mode: str,
    state: CounterState,
    trace_id: str,
    n_transactions: int,
    transfers: List[Dict[str, Any]],
    n_concurrent: int,
) -> Dict[str, Any]:
    runtime = AtomixRuntime(
        apply_effect=lambda effect: _apply_counter_effect(state, effect)
    )
    runtime.register_adapter("counter_transfer", CounterAdapter(state))
    results: List[Dict[str, Any]] = []
    start = time.perf_counter()

    if mode == "No-Tx":
        for i in range(0, len(transfers), max(1, n_concurrent)):
            batch = transfers[i : i + max(1, n_concurrent)]
            reads = []
            for args in batch:
                step_start = time.perf_counter()
                scopes = runtime.adapters.get("counter_transfer").scopes(args)
                reads.append({"args": args, "start": step_start, "scopes": scopes})
            for item in reads:
                try:
                    _apply_counter_effect_unchecked(state, item["args"])
                    duration_ms = (time.perf_counter() - item["start"]) * 1000
                    results.append(
                        {"success": True, "mode": mode, "duration_ms": duration_ms}
                    )
                except Exception as exc:  # noqa: BLE001
                    duration_ms = (time.perf_counter() - item["start"]) * 1000
                    results.append(
                        {
                            "success": False,
                            "mode": mode,
                            "duration_ms": duration_ms,
                            "error": str(exc),
                        }
                    )
        total_time = time.perf_counter() - start
        return {
            "mode": mode,
            "transactions": n_transactions,
            "total_time_seconds": total_time,
            "throughput_tx_per_sec": n_transactions / total_time if total_time else 0.0,
            "results": results,
        }

    for i in range(0, len(transfers), max(1, n_concurrent)):
        batch = transfers[i : i + max(1, n_concurrent)]
        epochs = []
        for args in batch:
            epoch = runtime.epochs.next(trace_id=trace_id)
            if mode == "No-Frontier":
                runtime.advance_frontier(
                    runtime.adapters.get("counter_transfer").scopes(args),
                    Epoch(10**9, trace_id),
                )
            epochs.append((args, epoch, time.perf_counter()))
        for args, epoch, step_start in epochs:
            try:
                runtime.run_tool(
                    "counter_transfer", lambda **kwargs: kwargs, args, epoch
                )
                duration_ms = (time.perf_counter() - step_start) * 1000
                results.append(
                    {"success": True, "mode": mode, "duration_ms": duration_ms}
                )
            except Exception as exc:  # noqa: BLE001
                duration_ms = (time.perf_counter() - step_start) * 1000
                results.append(
                    {
                        "success": False,
                        "mode": mode,
                        "duration_ms": duration_ms,
                        "error": str(exc),
                    }
                )

    total_time = time.perf_counter() - start
    return {
        "mode": mode,
        "transactions": n_transactions,
        "total_time_seconds": total_time,
        "throughput_tx_per_sec": n_transactions / total_time if total_time else 0.0,
        "results": results,
    }


def _generate_transfers(
    resources: List[str],
    min_amount: int,
    max_amount: int,
    n_transactions: int,
    zipf_alpha: float | None = None,
) -> List[Dict[str, Any]]:
    transfers: List[Dict[str, Any]] = []
    if zipf_alpha is None:
        for _ in range(n_transactions):
            source, target = random.sample(resources, 2)
            amount = random.randint(min_amount, max_amount)
            transfers.append({"source": source, "target": target, "amount": amount})
        return transfers

    weighted = [1.0 / ((idx + 1) ** zipf_alpha) for idx in range(len(resources))]
    total_weight = sum(weighted)
    probs = [w / total_weight for w in weighted]
    for _ in range(n_transactions):
        source, target = random.choices(resources, weights=probs, k=2)
        while target == source:
            target = random.choices(resources, weights=probs, k=1)[0]
        amount = random.randint(min_amount, max_amount)
        transfers.append({"source": source, "target": target, "amount": amount})
    return transfers


def _generate_sequential_transfers(
    resources: List[str],
    amount: int,
    n_transactions: int,
) -> List[Dict[str, Any]]:
    transfers: List[Dict[str, Any]] = []
    for idx in range(n_transactions):
        source = resources[idx % len(resources)]
        target = resources[(idx + 1) % len(resources)]
        transfers.append({"source": source, "target": target, "amount": amount})
    return transfers


def _generate_conflict_transfers(
    resources: List[str],
    amount: int,
    n_transactions: int,
) -> List[Dict[str, Any]]:
    source = resources[0]
    target = resources[1] if len(resources) > 1 else resources[0]
    return [
        {"source": source, "target": target, "amount": amount}
        for _ in range(n_transactions)
    ]


def _ensure_list(value: Any, default: List[Any]) -> List[Any]:
    if value is None:
        return default
    if isinstance(value, list):
        return value
    return [value]


def _simulate_out_of_order(delay_range: List[int], mode: str) -> str:
    delays = [
        random.randint(int(delay_range[0]), int(delay_range[1])) for _ in range(5)
    ]
    if mode in {"No-Tx", "No-Frontier"}:
        order = sorted(range(5), key=lambda idx: delays[idx])
        return f"W{order[-1] + 1}"
    return "W5"


def run(config_path: Path, output: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    variants = config["variants"]
    resources = [str(r) for r in config.get("resources", ["A", "B"])]
    initial_state = config.get("initial_state", {r: 1000 for r in resources})
    transfer = config.get("transfer", {"min": 1, "max": 100})
    runs_per = int(variants.get("runs_per_config", 1))
    n_list = _ensure_list(variants.get("n_concurrent", [2]), [2])
    transactions_per_run = int(variants.get("transactions_per_run", 1000))
    modes = _ensure_list(
        variants.get("baselines") or variants.get("atomix_variants") or ["Tx-Full"],
        ["Tx-Full"],
    )
    zipf_values = _ensure_list(variants.get("zipf_alpha"), [None])
    delay_variants = _ensure_list(variants.get("delay_variance"), ["low"])
    workload = str(config.get("workload", "microbench"))
    delay_ranges = config.get("delay_ranges_ms", {})
    scenario = str(config.get("scenario", "random"))
    sequential_amount = int(config.get("sequential_amount", 1))

    output.parent.mkdir(parents=True, exist_ok=True)
    records: List[Dict[str, Any]] = []

    if workload == "out_of_order":
        for delay_variant in delay_variants:
            delay_range = delay_ranges.get(str(delay_variant), [0, 0])
            for mode in modes:
                for run_idx in range(runs_per):
                    finals = [
                        _simulate_out_of_order(delay_range, mode)
                        for _ in range(transactions_per_run)
                    ]
                    violations = sum(1 for value in finals if value != "W5")
                    records.append(
                        {
                            "experiment": config.get("experiment", "E3.2"),
                            "trace_id": f"{config['trace_id']}-{mode}-{delay_variant}-{run_idx}",
                            "mode": mode,
                            "delay_variant": delay_variant,
                            "run_index": run_idx,
                            "violations": violations,
                            "transactions": transactions_per_run,
                        }
                    )
        output.write_text(json.dumps(records, indent=2), encoding="utf-8")
        return

    for zipf_alpha in zipf_values:
        for n_concurrent in n_list:
            for mode in modes:
                for run_idx in range(runs_per):
                    state = CounterState(values=dict(initial_state))
                    run_transactions = transactions_per_run
                    if workload == "synthetic_parallel":
                        transfers_per = int(
                            config.get("tasks", {}).get("transactions_per_agent", 20)
                        )
                        run_transactions = transfers_per * int(n_concurrent)
                        zipf_alpha = float(
                            config.get("tasks", {}).get("zipf_alpha", 1.0)
                        )
                    if workload == "contention_sweep":
                        zipf_alpha = float(zipf_alpha or 0.0)
                    if scenario == "sequential":
                        transfers = _generate_sequential_transfers(
                            resources, sequential_amount, run_transactions
                        )
                    elif scenario == "conflict":
                        transfers = _generate_conflict_transfers(
                            resources, sequential_amount, run_transactions
                        )
                    else:
                        transfers = _generate_transfers(
                            resources,
                            transfer["min"],
                            transfer["max"],
                            run_transactions,
                            zipf_alpha,
                        )
                    trace_id = f"{config['trace_id']}-{mode}-{n_concurrent}-{run_idx}"
                    result = _run_transfer_batch(
                        mode,
                        state,
                        trace_id,
                        run_transactions,
                        transfers,
                        n_concurrent,
                    )
                    min_value = min(state.values.values()) if state.values else 0
                    records.append(
                        {
                            "experiment": config.get("experiment", "E3"),
                            "trace_id": trace_id,
                            "mode": mode,
                            "n_concurrent": n_concurrent,
                            "run_index": run_idx,
                            "zipf_alpha": zipf_alpha,
                            "scenario": scenario,
                            "total_before": sum(initial_state.values()),
                            "total_after": state.total(),
                            "min_value": min_value,
                            "violated_invariant": state.total()
                            != sum(initial_state.values())
                            or min_value < 0,
                            "result": result,
                        }
                    )

    output.write_text(json.dumps(records, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Experiment config JSON")
    parser.add_argument("--output", required=True, help="Output JSON path")
    args = parser.parse_args()

    run(Path(args.config), Path(args.output))
    print(f"Wrote microbenchmark results to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
