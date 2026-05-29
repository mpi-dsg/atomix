"""WebArena final-state oracle (A2).

Diffs three sources:
  1. DOM snapshot of the target page(s) against pre-task baseline.
  2. Magento DB diff via the existing inspector container (HTTP API).
  3. Any side service touched (review-store, GitLab) via its API.

Skips session cookies and ephemeral state.

The oracle is intentionally substrate-agnostic at this layer: the harness
provides snapshot dicts and a `goal_fn` (typically WebArena's evaluator).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import OracleResult, ResidueRecord, register


# DOM: page_url -> {selector -> normalized_html_signature}
DOMSnapshot = Dict[str, Dict[str, str]]
# Magento: table -> {pk -> row}
MagentoSnapshot = Dict[str, Dict[Any, Dict[str, Any]]]
# Side service: service_name -> arbitrary JSON-able state
ServiceSnapshot = Dict[str, Any]


@dataclass
class WebArenaEvaluationContext:
    evaluation_fn: Optional[Callable[[], Any]] = None
    dom_before: Optional[DOMSnapshot] = None
    dom_after: Optional[DOMSnapshot] = None
    magento_before: Optional[MagentoSnapshot] = None
    magento_after: Optional[MagentoSnapshot] = None
    services_before: Optional[ServiceSnapshot] = None
    services_after: Optional[ServiceSnapshot] = None


def _diff_dom(before: DOMSnapshot, after: DOMSnapshot) -> List[ResidueRecord]:
    out: List[ResidueRecord] = []
    for url in sorted(set(before) | set(after)):
        b = before.get(url, {})
        a = after.get(url, {})
        for sel in sorted(set(a) - set(b)):
            out.append(
                ResidueRecord(
                    source="dom", key=f"{url}#{sel}", before=None, after=a[sel],
                    note="dom_create",
                )
            )
        for sel in sorted(set(b) - set(a)):
            out.append(
                ResidueRecord(
                    source="dom", key=f"{url}#{sel}", before=b[sel], after=None,
                    note="dom_delete",
                )
            )
        for sel in sorted(set(b) & set(a)):
            if b[sel] != a[sel]:
                out.append(
                    ResidueRecord(
                        source="dom", key=f"{url}#{sel}",
                        before=b[sel], after=a[sel], note="dom_modify",
                    )
                )
    return out


def _diff_magento(before: MagentoSnapshot, after: MagentoSnapshot) -> List[ResidueRecord]:
    out: List[ResidueRecord] = []
    for tbl in sorted(set(before) | set(after)):
        b = before.get(tbl, {})
        a = after.get(tbl, {})
        for pk in sorted(set(a) - set(b), key=str):
            out.append(
                ResidueRecord(
                    source="magento_db", key=f"{tbl}:{pk}",
                    before=None, after=a[pk], note="db_insert",
                )
            )
        for pk in sorted(set(b) - set(a), key=str):
            out.append(
                ResidueRecord(
                    source="magento_db", key=f"{tbl}:{pk}",
                    before=b[pk], after=None, note="db_delete",
                )
            )
        for pk in sorted(set(b) & set(a), key=str):
            if b[pk] != a[pk]:
                out.append(
                    ResidueRecord(
                        source="magento_db", key=f"{tbl}:{pk}",
                        before=b[pk], after=a[pk], note="db_modify",
                    )
                )
    return out


def _diff_services(before: ServiceSnapshot, after: ServiceSnapshot) -> List[ResidueRecord]:
    out: List[ResidueRecord] = []
    for name in sorted(set(before) | set(after)):
        b = before.get(name)
        a = after.get(name)
        if b != a:
            out.append(
                ResidueRecord(
                    source="service", key=name, before=b, after=a,
                    note="service_change",
                )
            )
    return out


def webarena_oracle(
    task_id: str,
    ctx: Optional[WebArenaEvaluationContext] = None,
    **kwargs,
) -> OracleResult:
    ctx = ctx or WebArenaEvaluationContext()
    goal = _eval_goal(ctx.evaluation_fn)
    residue: List[ResidueRecord] = []
    if ctx.dom_before is not None and ctx.dom_after is not None:
        residue.extend(_diff_dom(ctx.dom_before, ctx.dom_after))
    if ctx.magento_before is not None and ctx.magento_after is not None:
        residue.extend(_diff_magento(ctx.magento_before, ctx.magento_after))
    if ctx.services_before is not None and ctx.services_after is not None:
        residue.extend(_diff_services(ctx.services_before, ctx.services_after))
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


register("webarena", webarena_oracle)
