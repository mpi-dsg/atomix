"""
Compensation strategies for OSWorld actions.

Provides mechanisms to undo or reverse UI actions when transactions abort.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class CompensationContext:
    """Context for executing compensations."""

    vm_client: Any  # VMClient instance
    before_screenshot: Optional[bytes]
    after_screenshot: Optional[bytes]
    app_context: str
    action_type: str
    action_args: Dict[str, Any]
    compensation_info: Optional[Dict[str, Any]] = None


class CompensationExecutor(ABC):
    """Base class for compensation execution strategies."""

    @abstractmethod
    def can_compensate(self, context: CompensationContext) -> bool:
        """Check if this executor can handle the compensation."""
        ...

    @abstractmethod
    def execute(self, context: CompensationContext) -> bool:
        """Execute compensation, return success."""
        ...


class UndoShortcutCompensation(CompensationExecutor):
    """Compensate by sending Ctrl+Z (undo shortcut)."""

    # Apps known to support Ctrl+Z
    SUPPORTED_APPS = {
        "libreoffice",
        "vscode",
        "gimp",
        "chrome",
        "firefox",
        "gedit",
        "thunderbird",
    }

    def can_compensate(self, context: CompensationContext) -> bool:
        return context.app_context in self.SUPPORTED_APPS

    def execute(self, context: CompensationContext) -> bool:
        """Send Ctrl+Z to undo."""
        try:
            context.vm_client.hotkey(["ctrl", "z"])
            return True
        except Exception:
            return False


class ReverseActionCompensation(CompensationExecutor):
    """Compensate by performing the reverse action."""

    def can_compensate(self, context: CompensationContext) -> bool:
        info = context.compensation_info
        if not info:
            return False

        return info.get("type") in ["scroll", "reverse_drag", "hotkey"]

    def execute(self, context: CompensationContext) -> bool:
        """Execute reverse action."""
        info = context.compensation_info
        if not info:
            return False

        try:
            comp_type = info.get("type")

            if comp_type == "scroll":
                direction = info.get("direction")
                clicks = info.get("clicks", 3)
                context.vm_client.scroll(direction, clicks)
                return True

            elif comp_type == "reverse_drag":
                from_x = info.get("from_x")
                from_y = info.get("from_y")
                to_x = info.get("to_x")
                to_y = info.get("to_y")
                context.vm_client.drag_to(from_x, from_y, to_x, to_y)
                return True

            elif comp_type == "hotkey":
                keys = info.get("keys", [])
                context.vm_client.hotkey(keys)
                return True

        except Exception:
            return False

        return False


class EscapeKeyCompensation(CompensationExecutor):
    """Compensate by pressing Escape (dismiss dialogs/menus)."""

    def can_compensate(self, context: CompensationContext) -> bool:
        # Good for right-click context menus, modal dialogs
        info = context.compensation_info
        if info and info.get("type") == "press_key" and info.get("key") == "escape":
            return True

        # Also try for right-clicks
        return context.action_type == "right_click"

    def execute(self, context: CompensationContext) -> bool:
        """Press Escape key."""
        try:
            context.vm_client.press_key("escape")
            return True
        except Exception:
            return False


class BackspaceCompensation(CompensationExecutor):
    """Compensate typing by pressing backspace multiple times."""

    def can_compensate(self, context: CompensationContext) -> bool:
        info = context.compensation_info
        if not info:
            return False

        return info.get("type") == "undo_typing" and info.get("strategy") == "backspace"

    def execute(self, context: CompensationContext) -> bool:
        """Press backspace to delete typed text."""
        info = context.compensation_info
        if not info:
            return False

        try:
            text_length = info.get("text_length", 0)
            for _ in range(text_length):
                context.vm_client.press_key("backspace")
            return True
        except Exception:
            return False


class CompositeCompensation(CompensationExecutor):
    """Try multiple compensation strategies in order."""

    def __init__(self, strategies: List[CompensationExecutor]):
        self.strategies = strategies

    def can_compensate(self, context: CompensationContext) -> bool:
        return any(s.can_compensate(context) for s in self.strategies)

    def execute(self, context: CompensationContext) -> bool:
        """Try each strategy until one succeeds."""
        for strategy in self.strategies:
            if strategy.can_compensate(context):
                if strategy.execute(context):
                    return True
        return False


def build_default_compensator() -> CompositeCompensation:
    """Build the default compensation chain."""
    return CompositeCompensation(
        [
            ReverseActionCompensation(),  # Try reverse action first
            EscapeKeyCompensation(),  # Try escape for menus/dialogs
            UndoShortcutCompensation(),  # Try Ctrl+Z
            BackspaceCompensation(),  # Try backspace for typing
        ]
    )


class CompensationManager:
    """Manages compensation execution for aborted transactions."""

    def __init__(self, vm_client: Any):
        self.vm_client = vm_client
        self.compensator = build_default_compensator()

    def compensate(
        self,
        action_type: str,
        action_args: Dict[str, Any],
        app_context: str,
        compensation_info: Optional[Dict[str, Any]] = None,
        before_screenshot: Optional[bytes] = None,
        after_screenshot: Optional[bytes] = None,
    ) -> bool:
        """Attempt to compensate for an action."""
        context = CompensationContext(
            vm_client=self.vm_client,
            before_screenshot=before_screenshot,
            after_screenshot=after_screenshot,
            app_context=app_context,
            action_type=action_type,
            action_args=action_args,
            compensation_info=compensation_info,
        )

        return self.compensator.execute(context)
