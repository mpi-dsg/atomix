"""WebArena action adapter."""

from __future__ import annotations

from typing import Any, Set

from atomix.adapters import ToolAdapter
from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.tool_result import ToolResult


class WebArenaActionAdapter(ToolAdapter):
    name = "webarena_action"

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        site = args.get("site", "unknown")
        page = args.get("page_id") or args.get("url") or "unknown"
        element_id = args.get("element_id")
        scope = f"webarena:{site}:{page}"
        if element_id:
            scope = f"{scope}:element:{element_id}"
        return {scope}

    def to_effect(self, args: dict[str, Any], result: ToolResult, epoch: Epoch) -> Effect:
        payload = result.output if isinstance(result.output, dict) else {"output": result.output}
        action_type = args.get("action_type", "unknown")
        action = args.get("action") or args.get("raw_action") or args.get("action_str")
        return Effect(
            description=f"webarena:{action_type}@{epoch.value}",
            scopes=self.scopes(args),
            payload={
                "action_type": action_type,
                "args": args,
                "action": action,
                "result": payload,
            },
            idempotency_key=f"webarena:{action_type}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
            compensation=None,
        )
