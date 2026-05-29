"""In-memory buffered substrate for speculation experiments.

Branch effects land in a per-branch dict. On commit, the winning branch's
dict is merged into the global store. On abort, the dict is discarded.
A correctly-implemented Atomix should leave the global store identical to
its pre-task state on every aborted branch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class BufferedSubstrate:
    """Per-branch in-memory dict.

    The "global" store models the after-task observable state; the
    per-branch buffers are what speculation writes. Residue is non-empty
    only if a write from a losing branch reaches the global store.
    """

    global_store: Dict[str, str] = field(default_factory=dict)
    branches: Dict[str, Dict[str, str]] = field(default_factory=dict)

    def open_branch(self, branch_id: str) -> None:
        self.branches[branch_id] = {}

    def write(self, branch_id: str, key: str, value: str) -> None:
        self.branches.setdefault(branch_id, {})[key] = value

    def commit(self, branch_id: str) -> None:
        merged = self.branches.pop(branch_id, {})
        self.global_store.update(merged)

    def abort(self, branch_id: str) -> None:
        self.branches.pop(branch_id, None)

    def residue_keys(self, baseline_keys: set[str]) -> set[str]:
        """Keys present in the global store that were not in `baseline_keys`."""
        return set(self.global_store) - baseline_keys
