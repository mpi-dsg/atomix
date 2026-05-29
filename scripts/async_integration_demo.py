"""
Async integration demo using Atomix async helper.

Demonstrates buffering/commit under frontier control with async tool execution.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from atomix.adapters import ToolAdapter
from atomix.effects import Effect
from atomix.integrations.middleware import run_with_atomix_async
from atomix.logging import setup_logging
from atomix.runtime import AtomixRuntime

logger = logging.getLogger("atomix.async_demo")


@dataclass
class MemoryStore:
    data: Dict[str, Any] = field(default_factory=dict)
    journal: List[str] = field(default_factory=list)

    def apply_effect(self, effect: Effect) -> None:
        key = next(iter(effect.scopes))
        before = self.data.get(key)
        after = effect.payload["value"]
        self.data[key] = after
        self.journal.append(f"commit {key}: {before} -> {after}")


class WriteAdapter(ToolAdapter):
    name = "write_async"

    def scopes(self, args: dict[str, Any]) -> set[str]:
        return {args["key"]}

    def to_effect(self, args: dict[str, Any], result: Any, epoch) -> Effect:
        key = args["key"]
        return Effect(
            description=f"write:{key}@{epoch.value}",
            scopes={key},
            payload={"value": result.output},
            idempotency_key=f"{key}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
        )


async def fake_tool(value: str) -> str:
    await asyncio.sleep(0.01)
    return value


async def main() -> None:
    store = MemoryStore()
    runtime = AtomixRuntime(apply_effect=store.apply_effect)
    runtime.register_adapter("write_async", WriteAdapter())

    # Issue async calls in two branches
    res_a, tx_a = await run_with_atomix_async(
        runtime,
        "write_async",
        tool_fn=lambda **kwargs: fake_tool(kwargs["value"]),
        args={"key": "record", "value": "A"},
        trace_id="async_demo",
        branch_id="A",
    )
    res_b, tx_b = await run_with_atomix_async(
        runtime,
        "write_async",
        tool_fn=lambda **kwargs: fake_tool(kwargs["value"]),
        args={"key": "record", "value": "B"},
        trace_id="async_demo",
        branch_id="B",
    )
    runtime.tx_manager.abort(tx_b, "losing branch")
    runtime.advance_frontier({"record"}, tx_a.epoch)

    logger.info("Results: %s, %s", res_a, res_b)
    logger.info("State: %s", store.data)
    logger.info("Journal: %s", store.journal)


if __name__ == "__main__":
    setup_logging(level=logging.INFO)
    asyncio.run(main())
