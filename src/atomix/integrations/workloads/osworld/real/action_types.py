"""
OSWorld action type definitions.

Defines the 16 action types supported by OSWorld's pyautogui-based action server.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple


class ActionType(Enum):
    """All 16 OSWorld action types."""

    # Mouse actions (7)
    CLICK = "click"
    MOVE_TO = "move_to"
    DRAG_TO = "drag_to"
    RIGHT_CLICK = "right_click"
    DOUBLE_CLICK = "double_click"
    MOUSE_DOWN = "mouse_down"
    MOUSE_UP = "mouse_up"

    # Keyboard actions (6)
    TYPING = "typing"
    PRESS = "press"
    HOTKEY = "hotkey"
    KEY_DOWN = "key_down"
    KEY_UP = "key_up"

    # Scroll (1)
    SCROLL = "scroll"

    # Control actions (3)
    WAIT = "wait"
    FAIL = "fail"
    DONE = "done"


class CompensationStrategy(Enum):
    """Compensation strategy for an action type."""

    NONE = "none"  # No compensation possible (e.g., WAIT)
    UNDO_SHORTCUT = "undo_shortcut"  # Use Ctrl+Z
    REVERSE_ACTION = "reverse"  # Perform reverse action (e.g., scroll up -> down)
    BEST_EFFORT = "best_effort"  # Try multiple strategies


@dataclass
class OSWorldAction:
    """Unified action representation matching OSWorld's format."""

    action_type: ActionType

    # Mouse parameters
    coordinate: Optional[Tuple[int, int]] = None  # (x, y) for mouse actions
    start_coordinate: Optional[Tuple[int, int]] = None  # For drag operations
    button: str = "left"  # Mouse button: left, right, middle

    # Keyboard parameters
    text: Optional[str] = None  # For TYPING
    key: Optional[str] = None  # For PRESS, KEY_DOWN, KEY_UP
    keys: Optional[List[str]] = None  # For HOTKEY (e.g., ["ctrl", "c"])

    # Scroll parameters
    direction: Optional[str] = None  # up, down, left, right
    clicks: int = 3  # Number of scroll clicks

    # Control parameters
    duration: Optional[float] = None  # For WAIT (seconds)
    reason: Optional[str] = None  # For FAIL

    def to_payload(self) -> dict:
        """Convert to OSWorld action server payload format."""
        payload = {"action_type": self.action_type.value}

        if self.coordinate:
            payload["x"] = self.coordinate[0]
            payload["y"] = self.coordinate[1]

        if self.start_coordinate:
            payload["start_x"] = self.start_coordinate[0]
            payload["start_y"] = self.start_coordinate[1]

        if self.button != "left":
            payload["button"] = self.button

        if self.text is not None:
            payload["text"] = self.text

        if self.key is not None:
            payload["key"] = self.key

        if self.keys is not None:
            payload["keys"] = self.keys

        if self.direction is not None:
            payload["direction"] = self.direction
            payload["clicks"] = self.clicks

        if self.duration is not None:
            payload["duration"] = self.duration

        if self.reason is not None:
            payload["reason"] = self.reason

        return payload


# Action metadata: which actions are read-only, reversible, etc.
ACTION_METADATA = {
    ActionType.CLICK: {
        "has_side_effect": True,
        "default_compensation": CompensationStrategy.BEST_EFFORT,
        "scope_type": "mouse",
    },
    ActionType.MOVE_TO: {
        "has_side_effect": False,  # Just moves cursor
        "default_compensation": CompensationStrategy.NONE,
        "scope_type": "none",
    },
    ActionType.DRAG_TO: {
        "has_side_effect": True,
        "default_compensation": CompensationStrategy.REVERSE_ACTION,
        "scope_type": "mouse",
    },
    ActionType.RIGHT_CLICK: {
        "has_side_effect": True,
        "default_compensation": CompensationStrategy.BEST_EFFORT,
        "scope_type": "mouse",
    },
    ActionType.DOUBLE_CLICK: {
        "has_side_effect": True,
        "default_compensation": CompensationStrategy.BEST_EFFORT,
        "scope_type": "mouse",
    },
    ActionType.MOUSE_DOWN: {
        "has_side_effect": True,
        "default_compensation": CompensationStrategy.NONE,
        "scope_type": "mouse",
    },
    ActionType.MOUSE_UP: {
        "has_side_effect": True,
        "default_compensation": CompensationStrategy.NONE,
        "scope_type": "mouse",
    },
    ActionType.TYPING: {
        "has_side_effect": True,
        "default_compensation": CompensationStrategy.UNDO_SHORTCUT,
        "scope_type": "keyboard",
    },
    ActionType.PRESS: {
        "has_side_effect": True,
        "default_compensation": CompensationStrategy.UNDO_SHORTCUT,
        "scope_type": "keyboard",
    },
    ActionType.HOTKEY: {
        "has_side_effect": True,
        "default_compensation": CompensationStrategy.REVERSE_ACTION,
        "scope_type": "keyboard",
    },
    ActionType.KEY_DOWN: {
        "has_side_effect": True,
        "default_compensation": CompensationStrategy.NONE,
        "scope_type": "keyboard",
    },
    ActionType.KEY_UP: {
        "has_side_effect": True,
        "default_compensation": CompensationStrategy.NONE,
        "scope_type": "keyboard",
    },
    ActionType.SCROLL: {
        "has_side_effect": True,
        "default_compensation": CompensationStrategy.REVERSE_ACTION,
        "scope_type": "scroll",
    },
    ActionType.WAIT: {
        "has_side_effect": False,
        "default_compensation": CompensationStrategy.NONE,
        "scope_type": "none",
    },
    ActionType.FAIL: {
        "has_side_effect": False,
        "default_compensation": CompensationStrategy.NONE,
        "scope_type": "none",
    },
    ActionType.DONE: {
        "has_side_effect": False,
        "default_compensation": CompensationStrategy.NONE,
        "scope_type": "none",
    },
}


def to_pyautogui_command(action: OSWorldAction) -> str:
    action_type = action.action_type

    if action_type == ActionType.WAIT:
        return "WAIT"
    if action_type == ActionType.FAIL:
        return "FAIL"
    if action_type == ActionType.DONE:
        return "DONE"

    if action_type == ActionType.MOVE_TO:
        if not action.coordinate:
            raise ValueError("MOVE_TO requires coordinate")
        x, y = action.coordinate
        return f"pyautogui.moveTo({x}, {y})"

    if action_type == ActionType.CLICK:
        if action.coordinate:
            x, y = action.coordinate
            if action.button != "left":
                return f"pyautogui.click(x={x}, y={y}, button='{action.button}')"
            return f"pyautogui.click(x={x}, y={y})"
        if action.button != "left":
            return f"pyautogui.click(button='{action.button}')"
        return "pyautogui.click()"

    if action_type == ActionType.DOUBLE_CLICK:
        if not action.coordinate:
            return "pyautogui.doubleClick()"
        x, y = action.coordinate
        return f"pyautogui.doubleClick(x={x}, y={y})"

    if action_type == ActionType.RIGHT_CLICK:
        if not action.coordinate:
            return "pyautogui.rightClick()"
        x, y = action.coordinate
        return f"pyautogui.rightClick(x={x}, y={y})"

    if action_type == ActionType.MOUSE_DOWN:
        if action.button != "left":
            return f"pyautogui.mouseDown(button='{action.button}')"
        return "pyautogui.mouseDown()"

    if action_type == ActionType.MOUSE_UP:
        if action.button != "left":
            return f"pyautogui.mouseUp(button='{action.button}')"
        return "pyautogui.mouseUp()"

    if action_type == ActionType.DRAG_TO:
        if not action.coordinate:
            raise ValueError("DRAG_TO requires coordinate")
        end_x, end_y = action.coordinate
        if action.start_coordinate:
            start_x, start_y = action.start_coordinate
            return (
                f"pyautogui.moveTo({start_x}, {start_y}); "
                f"pyautogui.dragTo({end_x}, {end_y})"
            )
        return f"pyautogui.dragTo({end_x}, {end_y})"

    if action_type == ActionType.TYPING:
        if action.text is None:
            raise ValueError("TYPING requires text")
        return f"pyautogui.typewrite({repr(action.text)})"

    if action_type == ActionType.PRESS:
        if not action.key:
            raise ValueError("PRESS requires key")
        return f"pyautogui.press({repr(action.key)})"

    if action_type == ActionType.KEY_DOWN:
        if not action.key:
            raise ValueError("KEY_DOWN requires key")
        return f"pyautogui.keyDown({repr(action.key)})"

    if action_type == ActionType.KEY_UP:
        if not action.key:
            raise ValueError("KEY_UP requires key")
        return f"pyautogui.keyUp({repr(action.key)})"

    if action_type == ActionType.HOTKEY:
        if not action.keys:
            raise ValueError("HOTKEY requires keys")
        keys = ", ".join(repr(k) for k in action.keys)
        return f"pyautogui.hotkey({keys})"

    if action_type == ActionType.SCROLL:
        direction = action.direction or "down"
        clicks = action.clicks or 3
        if direction in {"left", "right"}:
            dx = clicks if direction == "right" else -clicks
            return f"pyautogui.hscroll({dx})"
        dy = clicks if direction == "up" else -clicks
        return f"pyautogui.vscroll({dy})"

    raise ValueError(f"Unsupported action type: {action_type}")
