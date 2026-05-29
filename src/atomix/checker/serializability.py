"""Detect cycles in a conflict graph (Tarjan SCC) and produce a report.

Public entry: `check_log(jsonl_path, substrate, canonicalize=True) ->
SerializabilityResult`.

The result includes a Clopper-Pearson 95% upper bound on the violation
rate over the schedules examined. The input log must come from the
harness's separate writer, not the adapter's record-effect path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .conflict_graph import ConflictGraph, OpRecord, build_graph
from .load_alias_suite import Substrate


@dataclass
class CycleReport:
    cycle: List[str]  # ordered tx_ids forming the cycle
    witness_ops: List[Tuple[OpRecord, OpRecord]]


@dataclass
class SerializabilityResult:
    schedules_checked: int
    violations_found: int
    upper_bound_95pct: float
    cycles: List[CycleReport] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schedules_checked": self.schedules_checked,
            "violations_found": self.violations_found,
            "upper_bound_95pct": self.upper_bound_95pct,
            "cycles": [
                {
                    "cycle": c.cycle,
                    "witness_ops": [
                        [_op_to_dict(a), _op_to_dict(b)] for (a, b) in c.witness_ops
                    ],
                }
                for c in self.cycles
            ],
        }


def _op_to_dict(o: OpRecord) -> dict:
    return {
        "tx_id": o.tx_id,
        "op_kind": o.op_kind,
        "scope": o.scope,
        "value_hash": o.value_hash,
        "ts": o.ts,
    }


# ---------- Tarjan SCC ----------


def _scc(graph: ConflictGraph) -> List[List[str]]:
    """Return strongly connected components with size >= 2 (i.e., cycles)
    or self-loops. Iterative Tarjan to avoid recursion limits on big logs.
    """
    index_counter = [0]
    stack: List[str] = []
    on_stack: Dict[str, bool] = {n: False for n in graph.nodes}
    indices: Dict[str, int] = {}
    lowlink: Dict[str, int] = {}
    result: List[List[str]] = []

    def strongconnect(start: str) -> None:
        # Iterative DFS using explicit work stack.
        work_stack: List[Tuple[str, Optional[str], List[str]]] = [
            (start, None, list(graph.edges.get(start, [])))
        ]
        indices[start] = index_counter[0]
        lowlink[start] = index_counter[0]
        index_counter[0] += 1
        stack.append(start)
        on_stack[start] = True
        while work_stack:
            v, parent, succs = work_stack[-1]
            if succs:
                w = succs.pop()
                if w not in indices:
                    indices[w] = index_counter[0]
                    lowlink[w] = index_counter[0]
                    index_counter[0] += 1
                    stack.append(w)
                    on_stack[w] = True
                    work_stack.append((w, v, list(graph.edges.get(w, []))))
                elif on_stack.get(w, False):
                    lowlink[v] = min(lowlink[v], indices[w])
            else:
                # Done with v.
                if lowlink[v] == indices[v]:
                    component = []
                    while True:
                        u = stack.pop()
                        on_stack[u] = False
                        component.append(u)
                        if u == v:
                            break
                    if len(component) >= 2 or (
                        len(component) == 1 and component[0] in graph.edges.get(component[0], [])
                    ):
                        result.append(component)
                work_stack.pop()
                if work_stack:
                    parent_node = work_stack[-1][0]
                    lowlink[parent_node] = min(lowlink[parent_node], lowlink[v])

    for n in list(graph.nodes):
        if n not in indices:
            strongconnect(n)
    return result


# ---------- Clopper-Pearson 95% upper bound ----------


def clopper_pearson_upper(k: int, n: int, alpha: float = 0.05) -> float:
    """Two-sided Clopper-Pearson upper bound on a Binomial(n, p) proportion.

    Returns p_upper such that Pr(X >= k | p=p_upper) = alpha/2.
    Uses scipy if available; falls back to a numeric beta-quantile via
    the regularized incomplete beta function from `math.lgamma`-based
    binary search if scipy is missing.
    """
    if n == 0:
        return 1.0
    if k == n:
        return 1.0
    try:
        from scipy.stats import beta  # type: ignore
        return float(beta.ppf(1 - alpha / 2, k + 1, n - k))
    except ImportError:
        return _beta_ppf_fallback(1 - alpha / 2, k + 1, n - k)


def _beta_ppf_fallback(q: float, a: float, b: float) -> float:
    """Bisection on the regularized incomplete beta CDF."""
    # Newton not needed; bisection is fine for a single CI bound.
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _ibeta(mid, a, b) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _ibeta(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta via continued fraction (Lentz)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    import math

    bt = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log(1.0 - x)
    )
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(x, a, b) / a
    return 1.0 - bt * _betacf(1.0 - x, b, a) / b


def _betacf(x: float, a: float, b: float, max_iter: int = 200, eps: float = 3e-16) -> float:
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


# ---------- Public entry ----------


def check_log(
    jsonl_path: Path,
    substrate: Substrate,
    canonicalize_scopes: bool = True,
    schedules_checked: Optional[int] = None,
) -> SerializabilityResult:
    """Read a JSONL log of OpRecords and return a serializability result.

    `schedules_checked` is the number of independent schedules contributing
    to the upper bound. If unset, we count distinct trace_ids in the log
    (one per run).
    """
    ops_by_trace: Dict[str, List[OpRecord]] = {}
    with Path(jsonl_path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            trace_id = d.get("trace_id") or "__default__"
            ops_by_trace.setdefault(trace_id, []).append(
                OpRecord(
                    tx_id=d["tx_id"],
                    op_kind=d["op_kind"],
                    scope=d["scope"],
                    value_hash=d.get("value_hash", ""),
                    ts=d["ts"],
                )
            )

    cycles: List[CycleReport] = []
    schedules_with_violations = 0
    for ops in ops_by_trace.values():
        g = build_graph(
            ops, substrate=substrate, canonicalize_scopes=canonicalize_scopes
        )
        sccs = _scc(g)
        schedule_had_violation = False
        for comp in sccs:
            # Reconstruct an ordered cycle through `comp` for the witness list.
            cyc = _extract_cycle(g, comp)
            if not cyc:
                continue
            schedule_had_violation = True
            witnesses = []
            for u, v in zip(cyc, cyc[1:] + cyc[:1]):
                wit = g.edge_witnesses.get((u, v))
                if wit:
                    witnesses.append(wit)
            cycles.append(CycleReport(cycle=cyc, witness_ops=witnesses))
        if schedule_had_violation:
            schedules_with_violations += 1

    n = schedules_checked if schedules_checked is not None else max(1, len(ops_by_trace))
    k = min(schedules_with_violations, n)
    upper = clopper_pearson_upper(k, n)

    return SerializabilityResult(
        schedules_checked=n,
        violations_found=schedules_with_violations,
        upper_bound_95pct=upper,
        cycles=cycles,
    )


def _extract_cycle(g: ConflictGraph, component: List[str]) -> List[str]:
    """Walk a cycle through nodes in `component` using DFS."""
    if not component:
        return []
    component_set = set(component)
    start = component[0]
    parent: Dict[str, str] = {}
    stack = [start]
    visited = {start}
    while stack:
        u = stack.pop()
        for w in g.edges.get(u, []):
            if w not in component_set:
                continue
            if w == start and u != start:
                # Reconstruct cycle: start -> ... -> u -> start
                path = [u]
                cur = u
                while cur != start:
                    cur = parent[cur]
                    path.append(cur)
                path.reverse()
                return path
            if w not in visited:
                visited.add(w)
                parent[w] = u
                stack.append(w)
    # Self-loop fallback
    if start in g.edges.get(start, []):
        return [start]
    return component
