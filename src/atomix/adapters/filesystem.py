from __future__ import annotations

from pathlib import Path
from typing import Any, Set

from ..effects import Effect
from ..epoch import Epoch
from ..tool_result import ToolResult
from . import ToolAdapter


class FileWriteAdapter(ToolAdapter):
    """Adapter for filesystem writes with compensation support."""

    name = "file_write"

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        return {str(Path(args["path"]).resolve())}

    def to_effect(self, args: dict[str, Any], result: ToolResult, epoch: Epoch) -> Effect:
        path = Path(args["path"]).resolve()
        payload = result.output if isinstance(result.output, dict) else {}
        before = payload.get("before")
        content = payload.get("content", result.output)

        def compensation() -> None:
            if before is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_text(before, encoding="utf-8")

        return Effect(
            description=f"write:{path}@{epoch.value}",
            scopes={str(path)},
            payload={"path": str(path), "content": content},
            idempotency_key=f"{path}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
            compensation=compensation,
        )
