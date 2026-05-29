"""Best-of-K speculation runner that drives a chosen substrate.

Used by E3 to sweep K ∈ {2, 4, 8, 16} × four effect classes × seven
baselines. The runner is deterministic: a controller picks the winning
branch by config, so there's no LLM in the critical path.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Protocol


class SpecSubstrate(Protocol):
    def open_branch(self, branch_id: str) -> Any: ...
    def commit(self, branch_id: str) -> Any: ...
    def abort(self, branch_id: str) -> Any: ...


@dataclass
class SpecBranchResult:
    branch_id: str
    won: bool
    aborted: bool
    error: Optional[str] = None
    residue_count: int = 0


@dataclass
class SpeculationRunner:
    """Generic best-of-K runner.

    `branch_action` is a callable invoked with (substrate, branch_id) for
    each branch and may write to the substrate. The harness picks one
    branch as the winner; the rest abort.
    """

    substrate: SpecSubstrate
    k: int = 4
    seed: int = 0

    def run(
        self,
        branch_action: Callable[[Any, str], None],
        winner_index: Optional[int] = None,
    ) -> List[SpecBranchResult]:
        rng = random.Random(self.seed)
        results: List[SpecBranchResult] = []
        branch_ids = [f"b{rng.randint(0, 2**31)}-{i}" for i in range(self.k)]
        if winner_index is None:
            winner_index = rng.randrange(self.k)
        for i, bid in enumerate(branch_ids):
            self.substrate.open_branch(bid)
            err: Optional[str] = None
            try:
                branch_action(self.substrate, bid)
            except Exception as e:
                err = str(e)
            won = (i == winner_index)
            if won and err is None:
                self.substrate.commit(bid)
                results.append(SpecBranchResult(branch_id=bid, won=True, aborted=False))
            else:
                self.substrate.abort(bid)
                results.append(
                    SpecBranchResult(
                        branch_id=bid, won=False, aborted=True, error=err,
                    )
                )
        return results
