"""
Framework-style integration demo using Atomix middleware helpers.

Simulates a fan-out/fan-in workflow with a winning branch and a losing branch.
Baseline immediately applies tool effects from both branches. Atomix buffers
effects by branch and only commits the winning branch once frontiers advance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from atomix.adapters import ToolAdapter
from atomix.effects import Effect
from atomix.integrations.middleware import run_with_atomix
from atomix.logging import setup_logging
from atomix.runtime import AtomixRuntime

logger = logging.getLogger("atomix.integration_demo")


@dataclass
class MemoryStore:
    data: Dict[str, Any] = field(default_factory=dict)
    journal: List[str] = field(default_factory=list)

    def apply_effect(self, effect: Effect) -> None:
        key = next(iter(effect.scopes))
        before = self.data.get(key)
        value = effect.payload["value"]
        self.data[key] = value
        self.journal.append(f"commit {effect.description} (before={before} after={value})")

    def snapshot(self) -> Dict[str, Any]:
        return dict(self.data)


class WriteAdapter(ToolAdapter):
    name = "write"

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def scopes(self, args: dict[str, Any]) -> set[str]:
        return {args["key"]}

    def to_effect(self, args: dict[str, Any], result: Any, epoch) -> Effect:
        key = args["key"]
        payload = result.output if hasattr(result, "output") else result
        value = payload["value"] if isinstance(payload, dict) else args["value"]
        return Effect(
            description=f"write:{key}->{value}@{epoch.value}",
            scopes={key},
            payload={"key": key, "value": value},
            idempotency_key=f"{key}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
        )


def fake_tool(**kwargs):
    return {"value": kwargs["value"]}


def baseline() -> MemoryStore:
    store = MemoryStore()
    store.data["record"] = "branch_a"
    store.journal.append("baseline applied branch_a")
    store.data["record"] = "branch_b"
    store.journal.append("baseline applied branch_b (contamination)")
    return store


def atomix() -> MemoryStore:
    store = MemoryStore()
    runtime = AtomixRuntime(apply_effect=store.apply_effect)
    runtime.register_adapter("write", WriteAdapter(store))

    # fan-out to two speculative branches
    trace_id = "integration_demo"
    (_, tx_a) = run_with_atomix(runtime, "write", fake_tool, {"key": "record", "value": "branch_a"}, trace_id, "A")
    (_, tx_b) = run_with_atomix(runtime, "write", fake_tool, {"key": "record", "value": "branch_b"}, trace_id, "B")

    # declare branch B losing, so abort its transaction
    runtime.tx_manager.abort(tx_b, "losing branch")

    # advance frontier for the resource to allow branch A to commit
    runtime.advance_frontier({"record"}, tx_a.epoch)
    return store


def main() -> None:
    setup_logging(level=logging.INFO)

    base = baseline()
    atm = atomix()

    logger.info("Baseline state: %s", base.snapshot())
    for line in base.journal:
        logger.info("  %s", line)
    logger.info("Atomix state: %s", atm.snapshot())
    for line in atm.journal:
        logger.info("  %s", line)


if __name__ == "__main__":
    main()
