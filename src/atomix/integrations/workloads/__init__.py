"""Workload-specific integration shims for Atomix."""

from .osworld import OSWORLD_TASKS, OSWorldHarness, Task
from .swebench import SwebenchHarness, SwebenchResult, SwebenchTask
from .webarena import WebArenaHarness, WebArenaTask

__all__ = [
    "OSWORLD_TASKS",
    "OSWorldHarness",
    "Task",
    "WebArenaHarness",
    "WebArenaTask",
    "SwebenchHarness",
    "SwebenchTask",
    "SwebenchResult",
]
