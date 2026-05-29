from __future__ import annotations

import asyncio

import pytest

from atomix.adapters import ToolAdapter
from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.integrations.middleware import run_with_atomix_async
from atomix.runtime import AtomixRuntime
from atomix.tool_result import ToolResult


class DummyAdapter(ToolAdapter):
    name = "dummy"

    def scopes(self, args):
        return {args["key"]}

    def to_effect(self, args, result: ToolResult, epoch):
        return Effect(
            description=f"dummy:{args['key']}",
            scopes={args["key"]},
            payload={"value": result.output},
            idempotency_key=f"{args['key']}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
        )

@pytest.mark.asyncio
async def test_run_with_atomix_async_auto_commits_without_branch():
    applied = []

    def apply(effect: Effect) -> None:
        applied.append(effect.payload["value"])

    runtime = AtomixRuntime(apply_effect=apply)
    runtime.register_adapter("dummy", DummyAdapter())

    async def tool(**kwargs):
        await asyncio.sleep(0)
        return "value"

    result, tx = await run_with_atomix_async(
        runtime, "dummy", tool_fn=tool, args={"key": "res"}, trace_id="t-async"
    )
    assert result == "value"
    assert tx.status == "committed"
    assert applied == ["value"]


@pytest.mark.asyncio
async def test_run_with_atomix_async_branch_waits_for_frontier():
    applied = []

    def apply(effect: Effect) -> None:
        applied.append(effect.payload["value"])

    runtime = AtomixRuntime(apply_effect=apply, effect_log_path=None)
    runtime.register_adapter("dummy", DummyAdapter())

    async def tool(**kwargs):
        await asyncio.sleep(0)
        return "value"

    result, tx = await run_with_atomix_async(
        runtime,
        "dummy",
        tool_fn=tool,
        args={"key": "res"},
        trace_id="t-async-branch",
        branch_id="candidate-a",
    )
    assert result == "value"
    assert tx.status == "waiting"
    assert applied == []

    epoch = Epoch(0, trace_id="t-async-branch", branch_id="candidate-a")
    runtime.advance_frontier({"res"}, epoch)
    assert applied == ["value"]
