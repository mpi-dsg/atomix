"""
OSWorld action adapters.

Each adapter wraps an OSWorld action type with Atomix transaction semantics,
providing scope resolution and compensation strategies.
"""

from .base import OSWorldActionAdapter
from .mouse import (
    ClickAdapter,
    MoveToAdapter,
    DragToAdapter,
    RightClickAdapter,
    DoubleClickAdapter,
    MouseDownAdapter,
    MouseUpAdapter,
)
from .keyboard import (
    TypingAdapter,
    PressAdapter,
    HotkeyAdapter,
    KeyDownAdapter,
    KeyUpAdapter,
)
from .scroll import ScrollAdapter
from .control import WaitAdapter, DoneAdapter, FailAdapter

__all__ = [
    "OSWorldActionAdapter",
    # Mouse
    "ClickAdapter",
    "MoveToAdapter",
    "DragToAdapter",
    "RightClickAdapter",
    "DoubleClickAdapter",
    "MouseDownAdapter",
    "MouseUpAdapter",
    # Keyboard
    "TypingAdapter",
    "PressAdapter",
    "HotkeyAdapter",
    "KeyDownAdapter",
    "KeyUpAdapter",
    # Scroll
    "ScrollAdapter",
    # Control
    "WaitAdapter",
    "DoneAdapter",
    "FailAdapter",
]

# Adapter registry for easy registration
ALL_ADAPTERS = {
    "click": ClickAdapter,
    "move_to": MoveToAdapter,
    "drag_to": DragToAdapter,
    "right_click": RightClickAdapter,
    "double_click": DoubleClickAdapter,
    "mouse_down": MouseDownAdapter,
    "mouse_up": MouseUpAdapter,
    "typing": TypingAdapter,
    "press": PressAdapter,
    "hotkey": HotkeyAdapter,
    "key_down": KeyDownAdapter,
    "key_up": KeyUpAdapter,
    "scroll": ScrollAdapter,
    "wait": WaitAdapter,
    "done": DoneAdapter,
    "fail": FailAdapter,
}


def register_all_adapters(runtime) -> None:
    """Register all OSWorld adapters with an AtomixRuntime."""
    for name, adapter_cls in ALL_ADAPTERS.items():
        runtime.register_adapter(name, adapter_cls())
