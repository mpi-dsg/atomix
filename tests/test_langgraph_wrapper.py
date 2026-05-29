"""Tests for LangGraph tool wrappers."""

from __future__ import annotations

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain_core")

from atomix.adapters import ToolAdapter
from atomix.effects import Effect
from atomix.integrations.langgraph import wrap_tool_function
from atomix.runtime import AtomixRuntime
from atomix.tool_result import ToolResult


class DummyAdapter(ToolAdapter):
    name = "dummy"

    def scopes(self, args):
        return {args["key"]}

    def to_effect(self, args, result: ToolResult, epoch):
        return Effect(
            description=f"dummy:{args['key']}@{epoch.value}",
            scopes={args["key"]},
            payload={"value": result.output["value"]},
            idempotency_key=f"{args['key']}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
        )


def test_wrap_tool_function_commits_after_frontier() -> None:
    applied = []
    runtime = AtomixRuntime(apply_effect=lambda e: applied.append(e.payload["value"]), effect_log_path=None)
    runtime.register_adapter("dummy", DummyAdapter())

    def tool(**kwargs):
        return {"value": kwargs["value"]}

    wrapped = wrap_tool_function(runtime, "dummy", tool)
    result = wrapped(
        {"key": "res", "value": "ok"},
        config={"configurable": {"trace_id": "t1"}},
    )

    assert result == {"value": "ok"}
    assert applied == ["ok"]
