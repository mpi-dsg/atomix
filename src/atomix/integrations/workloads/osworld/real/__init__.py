"""
Real OSWorld integration for Atomix.

Provides transactional semantics for the real OSWorld benchmark
(369 tasks across Ubuntu VMs).
"""

from .action_types import ActionType, OSWorldAction, CompensationStrategy
from .vm_client import VMClient, VMConfig, MockVMClient, ActionResult
from .harness import RealOSWorldHarness, RealOSWorldTask, RealTaskResult
from .state_tracker import StateTracker, UIStateSnapshot
from .scopes import ScopeResolver, UIScope, ScopeLevel
from .compensation import CompensationManager, build_default_compensator
from .agent import BaseAgent, ClaudeAgent, ScriptedAgent, RandomAgent
from .adapters import register_all_adapters, ALL_ADAPTERS
from .tasks import TaskLoader, load_tasks_from_osworld

__all__ = [
    # Action types
    "ActionType",
    "OSWorldAction",
    "CompensationStrategy",
    # VM client
    "VMClient",
    "VMConfig",
    "MockVMClient",
    "ActionResult",
    # Harness
    "RealOSWorldHarness",
    "RealOSWorldTask",
    "RealTaskResult",
    # State tracking
    "StateTracker",
    "UIStateSnapshot",
    # Scopes
    "ScopeResolver",
    "UIScope",
    "ScopeLevel",
    # Compensation
    "CompensationManager",
    "build_default_compensator",
    # Agents
    "BaseAgent",
    "ClaudeAgent",
    "ScriptedAgent",
    "RandomAgent",
    # Adapters
    "register_all_adapters",
    "ALL_ADAPTERS",
    # Tasks
    "TaskLoader",
    "load_tasks_from_osworld",
]
