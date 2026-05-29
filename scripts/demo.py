"""
Minimal Atomix demonstration showing speculative contamination avoidance and rollback.

Usage:
    python scripts/demo.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from atomix.adapters import ToolAdapter
from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.logging import setup_logging
from atomix.runtime import AtomixRuntime
from atomix.tool_result import ToolMeta, normalize_tool_result

logger = logging.getLogger("atomix.demo")


@dataclass
class InMemoryStore:
    data: Dict[str, Any] = field(default_factory=dict)
    journal: List[str] = field(default_factory=list)

    def plan_write(self, key: str, value: Any) -> Dict[str, Any]:
        """Dry-run a write; returns before/after without mutating state."""
        before = self.data.get(key)
        return {"key": key, "value": value, "before": before}

    def write_immediate(self, key: str, value: Any) -> None:
        before = self.data.get(key)
        self.data[key] = value
        self.journal.append(f"immediate write {key}: {before} -> {value}")

    def apply_effect(self, effect: Effect) -> None:
        key = next(iter(effect.scopes))
        value = effect.payload["value"]
        before = self.data.get(key)
        self.data[key] = value
        self.journal.append(f"commit {effect.description} (before={before}, after={value})")

    def restore(self, key: str, before: Any) -> None:
        if before is None:
            self.data.pop(key, None)
            self.journal.append(f"compensate delete {key}")
        else:
            self.data[key] = before
            self.journal.append(f"compensate restore {key} -> {before}")

    def snapshot(self) -> Dict[str, Any]:
        return dict(self.data)


class WriteAdapter(ToolAdapter):
    name = "write"

    def __init__(self, store: InMemoryStore) -> None:
        self.store = store

    def scopes(self, args: dict[str, Any]) -> set[str]:
        return {args["key"]}

    def to_effect(self, args: dict[str, Any], result: Any, epoch: Epoch) -> Effect:
        key = args["key"]
        payload = result.output if hasattr(result, "output") else result
        value = payload["value"]
        before = payload["before"]

        def compensation() -> None:
            self.store.restore(key, before)

        return Effect(
            description=f"write:{key}->{value}@{epoch.value}",
            scopes={key},
            payload={"key": key, "value": value},
            idempotency_key=f"{key}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
            compensation=compensation,
        )


def baseline_speculative() -> Tuple[Dict[str, Any], List[str]]:
    """Speculative branches write immediately; losing branch leaks effect."""
    store = InMemoryStore()
    store.write_immediate("record", "branch_a")  # winning branch
    store.write_immediate("record", "branch_b")  # losing branch contaminates
    return store.snapshot(), store.journal


def atomix_speculative() -> Tuple[Dict[str, Any], List[str]]:
    """Atomix buffers branch effects and aborts losing branch."""
    store = InMemoryStore()
    runtime = AtomixRuntime(apply_effect=store.apply_effect)
    adapter = WriteAdapter(store)
    runtime.register_adapter("write", adapter)

    base_epoch = Epoch(0, trace_id="spec_demo")
    _, tx_a = runtime.run_tool(
        "write", store.plan_write, {"key": "record", "value": "branch_a"}, base_epoch.for_branch("A")
    )
    _, tx_b = runtime.run_tool(
        "write", store.plan_write, {"key": "record", "value": "branch_b"}, base_epoch.for_branch("B")
    )

    runtime.tx_manager.abort(tx_b, "losing branch")
    runtime.advance_frontier({"record"}, base_epoch)  # release winning branch commit

    return store.snapshot(), store.journal


def baseline_partial_failure() -> Tuple[Dict[str, Any], List[str]]:
    """Baseline applies first effect even when later step fails."""
    store = InMemoryStore()
    try:
        store.write_immediate("order", "step1-applied")
        raise RuntimeError("failure after first step")
    except Exception as exc:  # noqa: BLE001
        store.journal.append(f"error: {exc}")
    return store.snapshot(), store.journal


def atomix_partial_failure() -> Tuple[Dict[str, Any], List[str]]:
    """Atomix groups steps and compensates on failure."""
    store = InMemoryStore()
    runtime = AtomixRuntime(apply_effect=store.apply_effect)
    adapter = WriteAdapter(store)
    runtime.register_adapter("write", adapter)

    epoch = Epoch(0, trace_id="partial_demo")
    tx = runtime.tx_manager.begin({"order"}, epoch)
    try:
        planned = store.plan_write("order", "step1-buffered")
        meta = ToolMeta(
            tool_name="write",
            trace_id=epoch.trace_id,
            branch_id=epoch.branch_id,
            attempt=0,
        )
        tool_result = normalize_tool_result(planned, meta=meta)
        effect = adapter.to_effect(
            {"key": "order", "value": "step1-buffered"}, tool_result, epoch
        )
        runtime.tx_manager.record_effect(tx, effect)
        raise RuntimeError("failure after first step")
    except Exception as exc:  # noqa: BLE001
        runtime.tx_manager.abort(tx, str(exc))
    return store.snapshot(), store.journal


def run_demo() -> None:
    setup_logging(level=logging.INFO)

    baseline_state, baseline_journal = baseline_speculative()
    atomix_state, atomix_journal = atomix_speculative()

    logger.info("=== Speculation Demo ===")
    logger.info("Baseline state: %s", baseline_state)
    logger.info("Baseline journal:")
    for line in baseline_journal:
        logger.info("  %s", line)
    logger.info("Atomix state: %s", atomix_state)
    logger.info("Atomix journal:")
    for line in atomix_journal:
        logger.info("  %s", line)

    logger.info("=== Partial Failure Demo ===")
    base_pf_state, base_pf_journal = baseline_partial_failure()
    atomix_pf_state, atomix_pf_journal = atomix_partial_failure()
    logger.info("Baseline state: %s", base_pf_state)
    for line in base_pf_journal:
        logger.info("  %s", line)
    logger.info("Atomix state: %s", atomix_pf_state)
    for line in atomix_pf_journal:
        logger.info("  %s", line)


if __name__ == "__main__":
    run_demo()
