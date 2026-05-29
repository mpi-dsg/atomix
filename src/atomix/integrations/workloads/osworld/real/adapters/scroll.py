"""
Scroll action adapter for OSWorld.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Set

from atomix.epoch import Epoch
from atomix.tool_result import ToolResult

from ..action_types import ActionType, OSWorldAction
from .base import OSWorldActionAdapter


class ScrollAdapter(OSWorldActionAdapter):
    """Adapter for SCROLL action."""

    action_type = ActionType.SCROLL
    name = "scroll"

    # Reverse directions
    REVERSE_DIRECTION = {
        "up": "down",
        "down": "up",
        "left": "right",
        "right": "left",
    }

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        return OSWorldAction(
            action_type=ActionType.SCROLL,
            direction=args.get("direction", "down"),
            clicks=args.get("clicks", 3),
        )

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        app_context = args.get("app_context", "desktop")
        return {f"osworld:ui:{app_context}:viewport"}

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        direction = args.get("direction", "down")
        clicks = args.get("clicks", 3)
        return f"scroll:{direction}:{clicks}@{epoch.value}"

    def build_compensation(
        self, args: dict[str, Any], result: ToolResult
    ) -> Optional[Callable[[], None]]:
        direction = args.get("direction", "down")
        clicks = args.get("clicks", 3)
        reverse_direction = self.REVERSE_DIRECTION.get(direction)

        if reverse_direction:
            def compensation_info():
                return {
                    "type": "scroll",
                    "direction": reverse_direction,
                    "clicks": clicks,
                }

            return compensation_info

        return None
