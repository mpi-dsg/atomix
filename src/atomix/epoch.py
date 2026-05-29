from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True, order=True)
class Epoch:
    """Logical time for artifacts and transactions.

    For the prototype we model epochs as monotonically increasing integers with
    optional branch identifiers for speculative runs.
    """

    value: int
    trace_id: str
    branch_id: Optional[str] = None

    def next(self) -> "Epoch":
        return Epoch(self.value + 1, self.trace_id, self.branch_id)

    def for_branch(self, branch_id: str) -> "Epoch":
        return Epoch(self.value, self.trace_id, branch_id)


class EpochManager:
    """Deterministic epoch generator keyed by trace and branch."""

    def __init__(self) -> None:
        self._counters: Dict[Tuple[str, Optional[str]], int] = {}

    def next(self, trace_id: str, branch_id: Optional[str] = None) -> Epoch:
        key = (trace_id, branch_id)
        current = self._counters.get(key, -1) + 1
        self._counters[key] = current
        return Epoch(current, trace_id=trace_id, branch_id=branch_id)
