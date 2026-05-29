"""τ-bench final-state oracle (A2).

Diffs SQL row state pre- vs. post-task on user-owned tables (orders,
customers, refunds). The oracle takes pre- and post-snapshot dicts so it
is decoupled from how the harness gets them (real DB, mock dict, JSONL).

Residue records carry (table, row_pk, op, payload).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import OracleResult, ResidueRecord, register


# table -> {pk -> row dict}
TableSnapshot = Dict[str, Dict[Any, Dict[str, Any]]]


@dataclass
class TauBenchEvaluationContext:
    evaluation_fn: Optional[Callable[[], Any]] = None
    db_before: Optional[TableSnapshot] = None
    db_after: Optional[TableSnapshot] = None
    user_tables: tuple = ("orders", "customers", "refunds")


def diff_db(
    before: TableSnapshot,
    after: TableSnapshot,
    tables: tuple = ("orders", "customers", "refunds"),
) -> List[ResidueRecord]:
    out: List[ResidueRecord] = []
    for tbl in tables:
        b = before.get(tbl, {})
        a = after.get(tbl, {})
        for pk in sorted(set(a) - set(b), key=str):
            out.append(
                ResidueRecord(
                    source="taubench_db", key=f"{tbl}:{pk}",
                    before=None, after=a[pk], note="insert",
                )
            )
        for pk in sorted(set(b) - set(a), key=str):
            out.append(
                ResidueRecord(
                    source="taubench_db", key=f"{tbl}:{pk}",
                    before=b[pk], after=None, note="delete",
                )
            )
        for pk in sorted(set(b) & set(a), key=str):
            if b[pk] != a[pk]:
                out.append(
                    ResidueRecord(
                        source="taubench_db", key=f"{tbl}:{pk}",
                        before=b[pk], after=a[pk], note="update",
                    )
                )
    return out


def taubench_oracle(
    task_id: str,
    ctx: Optional[TauBenchEvaluationContext] = None,
    **kwargs,
) -> OracleResult:
    ctx = ctx or TauBenchEvaluationContext()
    goal = _eval_goal(ctx.evaluation_fn)
    residue: List[ResidueRecord] = []
    if ctx.db_before is not None and ctx.db_after is not None:
        residue.extend(diff_db(ctx.db_before, ctx.db_after, ctx.user_tables))
    return OracleResult(goal_achieved=goal, residue=residue, details={"task_id": task_id})


def _eval_goal(fn: Optional[Callable[[], Any]]) -> bool:
    if fn is None:
        return False
    try:
        r = fn()
    except Exception:
        return False
    if isinstance(r, bool):
        return r
    if isinstance(r, dict):
        if "success" in r:
            return bool(r["success"])
        if "score" in r:
            return float(r["score"]) >= 1.0
        return bool(r)
    return bool(r)


register("taubench", taubench_oracle)
