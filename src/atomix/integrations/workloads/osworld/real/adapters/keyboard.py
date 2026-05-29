"""
Keyboard action adapters for OSWorld.

Covers: TYPING, PRESS, HOTKEY, KEY_DOWN, KEY_UP
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Set

from atomix.epoch import Epoch
from atomix.tool_result import ToolResult

from ..action_types import ActionType, OSWorldAction
from .base import OSWorldActionAdapter


class TypingAdapter(OSWorldActionAdapter):
    """Adapter for TYPING action - types text character by character."""

    action_type = ActionType.TYPING
    name = "typing"

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        return OSWorldAction(
            action_type=ActionType.TYPING,
            text=args["text"],
        )

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        app_context = args.get("app_context", "desktop")
        return {f"osworld:ui:{app_context}:text_input"}

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        text = args.get("text", "")
        preview = text[:20] + "..." if len(text) > 20 else text
        return f"typing:'{preview}'({len(text)} chars)@{epoch.value}"

    def build_compensation(
        self, args: dict[str, Any], result: ToolResult
    ) -> Optional[Callable[[], None]]:
        text = args.get("text", "")
        text_length = len(text)

        if text_length > 0:
            # Compensation: Ctrl+Z or select and delete
            def compensation_info():
                return {
                    "type": "undo_typing",
                    "text_length": text_length,
                    "strategy": "ctrl_z",  # or "backspace" for text_length times
                }

            return compensation_info

        return None


class PressAdapter(OSWorldActionAdapter):
    """Adapter for PRESS action - press and release a single key."""

    action_type = ActionType.PRESS
    name = "press"

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        return OSWorldAction(
            action_type=ActionType.PRESS,
            key=args["key"],
        )

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        key = args.get("key", "")
        app_context = args.get("app_context", "desktop")

        # Navigation keys don't really modify text
        nav_keys = {"up", "down", "left", "right", "home", "end", "pageup", "pagedown"}
        if key.lower() in nav_keys:
            return {f"osworld:ui:{app_context}:navigation"}

        # Modifier keys alone
        modifier_keys = {"shift", "ctrl", "alt", "command", "win"}
        if key.lower() in modifier_keys:
            return set()

        return {f"osworld:ui:{app_context}:text_input"}

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        key = args.get("key", "")
        return f"press:{key}@{epoch.value}"

    def build_compensation(
        self, args: dict[str, Any], result: ToolResult
    ) -> Optional[Callable[[], None]]:
        key = args.get("key", "").lower()

        # Some keys have obvious reverses
        reverse_keys = {
            "backspace": None,  # Can't easily undo
            "delete": None,
            "enter": None,  # Ctrl+Z might work
            "tab": None,
        }

        if key in reverse_keys:
            def compensation_info():
                return {"type": "ctrl_z"}

            return compensation_info

        return None


class HotkeyAdapter(OSWorldActionAdapter):
    """Adapter for HOTKEY action - key combinations like Ctrl+C, Ctrl+V."""

    action_type = ActionType.HOTKEY
    name = "hotkey"

    # Known reversible hotkey pairs
    REVERSE_HOTKEYS = {
        ("ctrl", "z"): ("ctrl", "y"),  # Undo -> Redo
        ("ctrl", "y"): ("ctrl", "z"),  # Redo -> Undo
        ("ctrl", "c"): None,  # Copy - read-only
        ("ctrl", "v"): ("ctrl", "z"),  # Paste -> Undo
        ("ctrl", "x"): ("ctrl", "z"),  # Cut -> Undo
        ("ctrl", "a"): None,  # Select All - doesn't modify
        ("ctrl", "s"): None,  # Save - can't easily undo
        ("ctrl", "n"): None,  # New - might close with Ctrl+W
        ("ctrl", "w"): None,  # Close - can't easily undo
        ("ctrl", "f"): None,  # Find - doesn't modify
        ("alt", "f4"): None,  # Close window - can't undo
    }

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        return OSWorldAction(
            action_type=ActionType.HOTKEY,
            keys=args["keys"],
        )

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        keys = [k.lower() for k in args.get("keys", [])]
        app_context = args.get("app_context", "desktop")

        scopes = set()

        # Clipboard operations have system-level scope
        if "ctrl" in keys and ("c" in keys or "x" in keys):
            scopes.add("osworld:system:clipboard")

        if "ctrl" in keys and "v" in keys:
            scopes.add("osworld:system:clipboard")
            scopes.add(f"osworld:ui:{app_context}:document")

        # Save affects the document
        if "ctrl" in keys and "s" in keys:
            scopes.add(f"osworld:ui:{app_context}:document")

        # Undo/Redo affect document
        if "ctrl" in keys and ("z" in keys or "y" in keys):
            scopes.add(f"osworld:ui:{app_context}:document")

        # Default: generic hotkey scope
        if not scopes:
            scopes.add(f"osworld:ui:{app_context}:hotkey")

        return scopes

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        keys = args.get("keys", [])
        return f"hotkey:{'+'.join(keys)}@{epoch.value}"

    def build_compensation(
        self, args: dict[str, Any], result: ToolResult
    ) -> Optional[Callable[[], None]]:
        keys = [k.lower() for k in args.get("keys", [])]
        keys_tuple = tuple(sorted(keys))

        reverse = self.REVERSE_HOTKEYS.get(keys_tuple)
        if reverse:
            def compensation_info():
                return {"type": "hotkey", "keys": list(reverse)}

            return compensation_info

        return None


class KeyDownAdapter(OSWorldActionAdapter):
    """Adapter for KEY_DOWN action - press key without releasing."""

    action_type = ActionType.KEY_DOWN
    name = "key_down"

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        return OSWorldAction(
            action_type=ActionType.KEY_DOWN,
            key=args["key"],
        )

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        # Key down alone usually doesn't have side effects
        # It's typically paired with key_up
        return set()

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        key = args.get("key", "")
        return f"key_down:{key}@{epoch.value}"


class KeyUpAdapter(OSWorldActionAdapter):
    """Adapter for KEY_UP action - release a pressed key."""

    action_type = ActionType.KEY_UP
    name = "key_up"

    def build_action(self, args: dict[str, Any]) -> OSWorldAction:
        return OSWorldAction(
            action_type=ActionType.KEY_UP,
            key=args["key"],
        )

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        return set()

    def _build_description(self, args: dict[str, Any], epoch: Epoch) -> str:
        key = args.get("key", "")
        return f"key_up:{key}@{epoch.value}"
