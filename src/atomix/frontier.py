from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Set, Tuple

from .epoch import Epoch
from .effects import ResourceId


@dataclass
class FrontierTracker:
    """Tracks per-resource progress frontiers.

    A transaction with epoch E may commit on resource r only if frontier[r] >= E.value.
    The orchestrator/integration layer is responsible for calling `advance` when
    it knows no future artifact affecting r will have an epoch smaller than E.
    """

    _frontiers: Dict[Tuple[str, str | None], Dict[ResourceId, int]] = field(default_factory=dict)
    _open_branches: Set[str] = field(default_factory=set)

    def can_commit(self, scopes: Set[ResourceId], epoch: Epoch) -> bool:
        frontiers = self._frontiers.get((epoch.trace_id, epoch.branch_id), {})
        for scope in scopes:
            frontier = frontiers.get(scope, -1)
            if epoch.value > frontier:
                return False
        return True

    def advance(self, scopes: Iterable[ResourceId], epoch: Epoch) -> None:
        """Advance frontiers for given scopes to at least epoch.value.

        Note: This class is not thread-safe. External synchronization is
        required if accessed from multiple threads.
        """
        frontiers = self._frontiers.setdefault((epoch.trace_id, epoch.branch_id), {})
        for scope in scopes:
            current = frontiers.get(scope, -1)
            if epoch.value > current:
                frontiers[scope] = epoch.value

    def frontier_for(
        self,
        scope: ResourceId,
        trace_id: str | None = None,
        branch_id: str | None = None,
    ) -> int:
        if trace_id is not None and branch_id is not None:
            return self._frontiers.get((trace_id, branch_id), {}).get(scope, -1)
        if trace_id is not None:
            matching = [
                frontiers.get(scope, -1)
                for (tid, _bid), frontiers in self._frontiers.items()
                if tid == trace_id
            ]
            return max(matching) if matching else -1
        if not self._frontiers:
            return -1
        return max(frontiers.get(scope, -1) for frontiers in self._frontiers.values())

    # Speculative branch helpers for integrations
    def open_branch(self, branch_id: str) -> None:
        self._open_branches.add(branch_id)

    def close_branch(self, branch_id: str) -> None:
        self._open_branches.discard(branch_id)

    def branches(self) -> Set[str]:
        return set(self._open_branches)
