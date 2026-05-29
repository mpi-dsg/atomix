"""Final-state oracles per benchmark (A2).

Public entry: `clean_success(benchmark, task_id, **kwargs) -> (bool, [ResidueRecord])`.

Each oracle compares pre- and post-task state for its benchmark, returns
`(goal_achieved, residue_list)`. A "clean" success has goal_achieved=True
AND residue=[]. The runners use this oracle as the success metric in E1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple


@dataclass(frozen=True)
class ResidueRecord:
    """A piece of state left behind by a task that the oracle considers residue.

    Generic across benchmarks. Per-benchmark oracles populate `source` and
    `key` consistently so reports group by source.
    """

    source: str  # benchmark-specific: "fs", "magento_db", "taubench_db", "process", ...
    key: str  # canonical identifier within source
    before: Any
    after: Any
    note: str = ""


@dataclass
class OracleResult:
    goal_achieved: bool
    residue: List[ResidueRecord] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def is_clean(self) -> bool:
        return self.goal_achieved and not self.residue


_ORACLES: Dict[str, Callable[..., OracleResult]] = {}


def register(name: str, fn: Callable[..., OracleResult]) -> None:
    _ORACLES[name] = fn


def clean_success(benchmark: str, task_id: str, **kwargs) -> Tuple[bool, List[ResidueRecord]]:
    """Run the oracle for `benchmark` on `task_id`. Returns (clean_success, residue).

    `clean_success` is True iff goal_achieved and residue is empty.
    """
    if benchmark not in _ORACLES:
        raise KeyError(f"No oracle registered for benchmark={benchmark}. "
                       f"Known: {list(_ORACLES)}")
    result = _ORACLES[benchmark](task_id=task_id, **kwargs)
    return result.is_clean(), list(result.residue)


# Lazy registration via subpackage imports — keeps optional deps out of the hot path.
def _bootstrap() -> None:
    from . import osworld, taubench, webarena  # noqa: F401  (registers via side effect)


_bootstrap()


__all__ = ["ResidueRecord", "OracleResult", "clean_success", "register"]
