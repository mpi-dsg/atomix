"""
Mouse action adapters for OSWorld.

Covers: CLICK, MOVE_TO, DRAG_TO, RIGHT_CLICK, DOUBLE_CLICK, MOUSE_DOWN, MOUSE_UP
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Set

from atomix.epoch import Epoch
from atomix.tool_result import ToolResult

from ..action_types import ActionType, OSWorldAction
from .base import OSWorldActionAdapter


class ClickAdapter(OSWorldActionAdapter):
    """Adapter for CLICK action."""

    action_type = ActionType.CLICK
    name = "click"

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        return OSWorldAction(
            action_type=ActionType.CLICK,
            coordinate=(args["x"], args["y"]),
            button=args.get("button", "left"),
        )

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        x, y = args.get("x", 0), args.get("y", 0)
        return f"click:({x},{y})@{epoch.value}"

    def build_compensation(
        self, args: dict[str, Any], result: ToolResult
    ) -> Optional[Callable[[], None]]:
        # Click compensation is best-effort: might try Escape to close any opened menu
        # Actual compensation depends on context and is handled by CompensationExecutor
        return None  # Handled externally


class MoveToAdapter(OSWorldActionAdapter):
    """Adapter for MOVE_TO action - cursor movement only, no click."""

    action_type = ActionType.MOVE_TO
    name = "move_to"

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        return OSWorldAction(
            action_type=ActionType.MOVE_TO,
            coordinate=(args["x"], args["y"]),
        )

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        # Move-only has no side effects
        return set()

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        x, y = args.get("x", 0), args.get("y", 0)
        return f"move_to:({x},{y})@{epoch.value}"


class DragToAdapter(OSWorldActionAdapter):
    """Adapter for DRAG_TO action - drag from current/start position to end."""

    action_type = ActionType.DRAG_TO
    name = "drag_to"

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        end_x, end_y = args["end_x"], args["end_y"]
        start_x = args.get("start_x")
        start_y = args.get("start_y")

        has_start = start_x is not None and start_y is not None
        return OSWorldAction(
            action_type=ActionType.DRAG_TO,
            coordinate=(end_x, end_y),
            start_coordinate=(start_x, start_y) if has_start else None,
        )

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        app_context = args.get("app_context", "desktop")
        # Drag affects both start and end regions
        start_x = args.get("start_x", 0)
        start_y = args.get("start_y", 0)
        end_x = args.get("end_x", 0)
        end_y = args.get("end_y", 0)

        start_region = f"{start_x // 200}_{start_y // 200}"
        end_region = f"{end_x // 200}_{end_y // 200}"

        scopes = {f"osworld:ui:{app_context}:mouse:{start_region}"}
        if start_region != end_region:
            scopes.add(f"osworld:ui:{app_context}:mouse:{end_region}")

        return scopes

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        start_x = args.get("start_x", "?")
        start_y = args.get("start_y", "?")
        end_x = args.get("end_x", 0)
        end_y = args.get("end_y", 0)
        return f"drag:({start_x},{start_y})->({end_x},{end_y})@{epoch.value}"

    def build_compensation(
        self, args: dict[str, Any], result: ToolResult
    ) -> Optional[Callable[[], None]]:
        # Drag can be reversed by dragging back
        # Store reverse drag info in closure
        start_x = args.get("start_x")
        start_y = args.get("start_y")
        end_x = args.get("end_x")
        end_y = args.get("end_y")

        if all(value is not None for value in (start_x, start_y, end_x, end_y)):
            # Return info for external executor to perform reverse drag
            def compensation_info():
                return {
                    "type": "reverse_drag",
                    "from_x": end_x,
                    "from_y": end_y,
                    "to_x": start_x,
                    "to_y": start_y,
                }

            return compensation_info

        return None


class RightClickAdapter(OSWorldActionAdapter):
    """Adapter for RIGHT_CLICK action."""

    action_type = ActionType.RIGHT_CLICK
    name = "right_click"

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        return OSWorldAction(
            action_type=ActionType.RIGHT_CLICK,
            coordinate=(args["x"], args["y"]),
        )

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        x, y = args.get("x", 0), args.get("y", 0)
        return f"right_click:({x},{y})@{epoch.value}"

    def build_compensation(
        self, args: dict[str, Any], result: ToolResult
    ) -> Optional[Callable[[], None]]:
        # Right-click typically opens context menu - can dismiss with Escape
        def compensation_info():
            return {"type": "press_key", "key": "escape"}

        return compensation_info


class DoubleClickAdapter(OSWorldActionAdapter):
    """Adapter for DOUBLE_CLICK action."""

    action_type = ActionType.DOUBLE_CLICK
    name = "double_click"

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        return OSWorldAction(
            action_type=ActionType.DOUBLE_CLICK,
            coordinate=(args["x"], args["y"]),
        )

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        x, y = args.get("x", 0), args.get("y", 0)
        return f"double_click:({x},{y})@{epoch.value}"


class MouseDownAdapter(OSWorldActionAdapter):
    """Adapter for MOUSE_DOWN action - press without release."""

    action_type = ActionType.MOUSE_DOWN
    name = "mouse_down"

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        return OSWorldAction(
            action_type=ActionType.MOUSE_DOWN,
            coordinate=(args.get("x"), args.get("y")) if "x" in args else None,
            button=args.get("button", "left"),
        )

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        x = args.get("x", "current")
        y = args.get("y", "current")
        return f"mouse_down:({x},{y})@{epoch.value}"


class MouseUpAdapter(OSWorldActionAdapter):
    """Adapter for MOUSE_UP action - release button."""

    action_type = ActionType.MOUSE_UP
    name = "mouse_up"

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        return OSWorldAction(
            action_type=ActionType.MOUSE_UP,
            coordinate=(args.get("x"), args.get("y")) if "x" in args else None,
            button=args.get("button", "left"),
        )

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        x = args.get("x", "current")
        y = args.get("y", "current")
        return f"mouse_up:({x},{y})@{epoch.value}"
