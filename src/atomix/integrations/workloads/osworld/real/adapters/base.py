"""
Base adapter class for OSWorld actions.

Extends Atomix's ToolAdapter with OSWorld-specific behavior for
scope resolution and compensation.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Callable, Optional, Set

from atomix.adapters import ToolAdapter
from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.tool_result import ToolResult

from ..action_types import ActionType, CompensationStrategy, OSWorldAction, ACTION_METADATA


class OSWorldActionAdapter(ToolAdapter):
    """Base adapter for OSWorld GUI actions."""

    # Subclasses should set these
    action_type: ActionType
    name: str = ""

    def __init__(self):
        if not self.name:
            self.name = self.action_type.value

    @property
    def metadata(self) -> dict:
        """Get action metadata."""
        return ACTION_METADATA.get(self.action_type, {})

    @property
    def compensation_strategy(self) -> CompensationStrategy:
        """Get default compensation strategy for this action."""
        return self.metadata.get("default_compensation", CompensationStrategy.NONE)

    @property
    def scope_type(self) -> str:
        """Get scope type (mouse, keyboard, scroll, none)."""
        return self.metadata.get("scope_type", "none")

    @property
    def has_side_effect(self) -> bool:
        """Whether this action has side effects."""
        return self.metadata.get("has_side_effect", True)

    @abstractmethod
    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        """Build an OSWorldAction from tool arguments."""
        ...

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        """Default scope resolution based on action type and context."""
        if self.scope_type == "none" or not self.has_side_effect:
            return set()

        app_context = args.get("app_context", "desktop")
        scope_type = self.scope_type

        # Build scope based on action type
        if scope_type == "mouse":
            # Mouse actions scope to application + approximate region
            x = args.get("x", 0)
            y = args.get("y", 0)
            region = f"{x // 200}_{y // 200}"  # Grid-based approximation
            return {f"osworld:ui:{app_context}:mouse:{region}"}

        elif scope_type == "keyboard":
            # Keyboard input scopes to application's text input
            return {f"osworld:ui:{app_context}:text_input"}

        elif scope_type == "scroll":
            # Scroll scopes to application's viewport
            return {f"osworld:ui:{app_context}:viewport"}

        return {f"osworld:ui:{app_context}:{scope_type}"}

    def to_effect(self, args: dict[str, Any], result: ToolResult, epoch: Epoch) -> Effect:
        """Convert action result to an Effect."""
        self.build_action(args)
        scopes = self.scopes(args)

        # Build compensation if supported
        compensation = self.build_compensation(args, result)

        # Build idempotency key
        idem_key = self._build_idempotency_key(args, epoch)

        return Effect(
            description=self._build_description(args, epoch),
            scopes=scopes,
            payload=self._build_payload(args, result),
            idempotency_key=idem_key,
            compensation=compensation,
        )

    def build_compensation(
        self,
        args: dict[str, Any],
        result: ToolResult,
    ) -> Optional[Callable[[], None]]:
        """Build compensation callback. Override in subclasses for specific behavior."""
        if self.compensation_strategy == CompensationStrategy.NONE:
            return None

        # Default: return None, subclasses implement specific compensations
        return None

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        """Build human-readable effect description."""
        return f"{self.action_type.value}@{epoch.value}"

    def _build_idempotency_key(self, args: dict[str, Any], epoch: Epoch) -> str:
        """Build idempotency key for deduplication."""
        # Include key args in idempotency key
        key_parts = [self.action_type.value]

        if "x" in args and "y" in args:
            key_parts.append(f"{args['x']}_{args['y']}")
        if "text" in args:
            key_parts.append(f"text_{hash(args['text'])}")
        if "key" in args:
            key_parts.append(f"key_{args['key']}")
        if "keys" in args:
            key_parts.append(f"keys_{'_'.join(args['keys'])}")

        key_parts.append(epoch.trace_id)
        key_parts.append(str(epoch.value))
        key_parts.append(epoch.branch_id or "main")

        return ":".join(key_parts)

    def _build_payload(self, args: dict[str, Any], result: ToolResult) -> dict[str, Any]:
        """Build effect payload with action details and result."""
        payload = {
            "action_type": self.action_type.value,
            "args": args,
        }

        # Include screenshots if available
        if isinstance(result.output, dict):
            if "before_screenshot" in result.output:
                payload["has_before_screenshot"] = (
                    result.output["before_screenshot"] is not None
                )
            if "after_screenshot" in result.output:
                payload["has_after_screenshot"] = (
                    result.output["after_screenshot"] is not None
                )
            if "response" in result.output:
                payload["response"] = result.output["response"]

        return payload
