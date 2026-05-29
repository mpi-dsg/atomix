"""Conflict-serializability checker (A5) and adversarial alias suite (A6).

The checker reads from the harness's *independent* logging path (not from
the adapter's record-effect path) and reports conflict-serializability
violations with witness ops + a Clopper-Pearson 95% upper bound.

The checker assumes the input log comes from an independent writer so it can
audit the adapter record-effect path rather than reuse it.
"""

from .conflict_graph import ConflictGraph, OpRecord, build_graph
from .serializability import SerializabilityResult, check_log
from .load_alias_suite import AliasCase, load_alias_suite, canonicalize

__all__ = [
    "AliasCase",
    "ConflictGraph",
    "OpRecord",
    "SerializabilityResult",
    "build_graph",
    "canonicalize",
    "check_log",
    "load_alias_suite",
]
