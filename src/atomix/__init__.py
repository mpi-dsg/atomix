"""
Atomix: transactional tool semantics with frontier-aligned commits.

Core entrypoints:
- AtomixRuntime: orchestrates frontier tracking, transactions, adapters, and fault injection.
- FrontierTracker: per-resource logical frontiers derived from orchestrator events.
- TransactionManager: begin/record/commit/abort tool effects with isolation and compensations.
"""

__version__ = "0.0.1"

from .effects import Effect, EffectReversibility
from .epoch import Epoch
from .logging import get_logger, setup_logging
from .runtime import AtomixRuntime

__all__ = [
    "AtomixRuntime",
    "Effect",
    "EffectReversibility",
    "Epoch",
    "__version__",
    "get_logger",
    "setup_logging",
]
