"""
Transactional decorator for Anthropic Messages API tool use.

Maps:
- trace_id: caller-provided per-run ID.
- branch_id: Anthropic tool_use_id per tool invocation.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict
from uuid import uuid4

from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.logging import setup_logging
from atomix.runtime import AtomixRuntime
from atomix.transactions import CommitBlocked
from atomix.tool_result import ToolMeta, normalize_tool_result

logger = logging.getLogger("atomix.anthropic_middleware")

ToolFn = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


class AtomixToolMiddleware:
    def __init__(self, runtime: AtomixRuntime) -> None:
        self.runtime = runtime

    def wrap(self, tool_name: str, fn: ToolFn) -> ToolFn:
        async def wrapper(args: Dict[str, Any], *, tool_use_id: str, trace_id: str) -> Dict[str, Any]:
            epoch = self.runtime.epochs.next(trace_id=trace_id, branch_id=tool_use_id)
            adapter = self.runtime.adapters.get(tool_name)
            scopes = adapter.scopes(args)
            tx = self.runtime.tx_manager.begin(scopes, epoch)
            try:
                result = await fn(args)
                meta = ToolMeta(
                    tool_name=tool_name,
                    trace_id=epoch.trace_id,
                    branch_id=epoch.branch_id,
                    attempt=0,
                )
                tool_result = normalize_tool_result(result, meta=meta)
                effect = adapter.to_effect(args, tool_result, epoch)
                self.runtime.tx_manager.record_effect(tx, effect)
                self.runtime.advance_frontier(scopes, epoch)
                self.runtime.tx_manager.commit(tx)
                return result
            except CommitBlocked:
                return result
            except Exception as exc:  # noqa: BLE001
                self.runtime.tx_manager.abort(tx, str(exc))
                raise

        return wrapper


# Example adapter and usage
if __name__ == "__main__":
    import asyncio

    from atomix.adapters import ToolAdapter

    class DummyAdapter(ToolAdapter):
        name = "echo_tool"

        def scopes(self, args):
            return {args.get("key", "default")}

        def to_effect(self, args, result, epoch: Epoch) -> Effect:
            return Effect(
                description=f"echo:{args}",
                scopes={args.get("key", "default")},
                payload={"value": result.output},
                idempotency_key=f"{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
            )

    async def echo(args):
        return {"echo": args}

    runtime = AtomixRuntime(apply_effect=lambda eff: None)
    runtime.register_adapter("echo_tool", DummyAdapter())
    middleware = AtomixToolMiddleware(runtime)
    wrapped = middleware.wrap("echo_tool", echo)

    async def demo():
        res = await wrapped({"key": "k1"}, tool_use_id="tool-1", trace_id=uuid4().hex)
        logger.info("Result: %s", res)

    setup_logging(level=logging.INFO)
    asyncio.run(demo())
