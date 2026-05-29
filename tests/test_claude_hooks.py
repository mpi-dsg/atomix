"""Tests for Claude Code hook helpers."""

from __future__ import annotations

from pathlib import Path

from atomix.integrations.claude_code import begin_tool_call, complete_tool_call
from atomix.runtime import AtomixRuntime
from atomix.store import SqliteStore
from atomix.adapters import ToolAdapter
from atomix.effects import Effect


class DummyAdapter(ToolAdapter):
    name = "dummy"

    def scopes(self, args):
        return {"scope:dummy:" + args["key"]}

    def to_effect(self, args, result, epoch):
        return Effect(
            description=f"dummy:{args['key']}@{epoch.value}",
            scopes={"scope:dummy:" + args["key"]},
            payload={"value": result.output},
            idempotency_key=f"{args['key']}:{epoch.trace_id}:{epoch.value}",
        )


def test_hooks_commit_after_frontier_advance(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "atomix.sqlite")
    applied = []

    runtime = AtomixRuntime(
        apply_effect=lambda e: applied.append(e),
        effect_log_path=None,
        store=store,
    )
    runtime.register_adapter("dummy", DummyAdapter())

    begin_payload = {
        "session_id": "s1",
        "tool_use_id": "u1",
        "tool_name": "dummy",
        "tool_input": {"key": "k1"},
    }
    begin_tool_call(begin_payload, runtime=runtime, store=store)

    post_payload = {
        "session_id": "s1",
        "tool_use_id": "u1",
        "tool_name": "dummy",
        "tool_input": {"key": "k1"},
        "tool_response": {"success": True, "output": "ok"},
    }
    outcome = complete_tool_call(post_payload, runtime=runtime, store=store)

    assert outcome.status == "committed"
    assert len(applied) == 1
