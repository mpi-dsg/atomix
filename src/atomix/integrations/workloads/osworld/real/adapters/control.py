"""
Control action adapters for OSWorld.

Covers: WAIT, DONE, FAIL - these are signals/pauses, not UI interactions.
"""

from __future__ import annotations

from typing import Any, Set

from atomix.epoch import Epoch

from ..action_types import ActionType, OSWorldAction
from .base import OSWorldActionAdapter


class WaitAdapter(OSWorldActionAdapter):
    """Adapter for WAIT action - pause execution."""

    action_type = ActionType.WAIT
    name = "wait"

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        return OSWorldAction(
            action_type=ActionType.WAIT,
            duration=args.get("duration", 1.0),
        )

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        # Wait is passive - no side effects
        return set()

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        duration = args.get("duration", 1.0)
        return f"wait:{duration}s@{epoch.value}"


class DoneAdapter(OSWorldActionAdapter):
    """Adapter for DONE action - signals task completion."""

    action_type = ActionType.DONE
    name = "done"

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        return OSWorldAction(action_type=ActionType.DONE)

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        # Signal only - no side effects
        return set()

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        return f"done@{epoch.value}"


class FailAdapter(OSWorldActionAdapter):
    """Adapter for FAIL action - signals task failure."""

    action_type = ActionType.FAIL
    name = "fail"

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        return OSWorldAction(
            action_type=ActionType.FAIL,
            reason=args.get("reason", "unknown"),
        )

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        # Signal only - no side effects
        return set()

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        reason = args.get("reason", "unknown")
        return f"fail:{reason}@{epoch.value}"
