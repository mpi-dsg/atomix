"""Build a conflict graph from an operation log.

A conflict edge is added between two transactions if they touch the same
(canonicalized) scope and at least one is a write. The order of the edge
follows the operations' timestamps. Cycles in this graph are
conflict-serializability violations.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Set, Tuple

from .load_alias_suite import Substrate, canonicalize


@dataclass(frozen=True)
class OpRecord:
    """A single operation read from the harness's independent log path.

    Fields match the checker JSONL schema consumed by `check_log`.
    """

    tx_id: str
    op_kind: str  # read | write | commit | abort
    scope: str
    value_hash: str
    ts: str  # ISO-8601


@dataclass
class ConflictGraph:
    """Directed conflict graph between transactions.

    Vertex set: tx_ids. Edge u -> v iff some op in u conflicts with a later
    op in v on the same canonical scope.
    """

    nodes: Set[str] = field(default_factory=set)
    edges: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))
    edge_witnesses: Dict[Tuple[str, str], Tuple[OpRecord, OpRecord]] = field(
        default_factory=dict
    )

    def add_edge(self, u: str, v: str, witness: Tuple[OpRecord, OpRecord]) -> None:
        if u == v:
            return
        self.nodes.add(u)
        self.nodes.add(v)
        self.edges[u].add(v)
        # Keep the first witness only — sufficient for a cycle report.
        self.edge_witnesses.setdefault((u, v), witness)


def build_graph(
    ops: Iterable[OpRecord],
    substrate: Substrate,
    canonicalize_scopes: bool = True,
) -> ConflictGraph:
    """Construct a conflict graph from a stream of operations.

    Args:
        ops: operations in any order; we sort by timestamp internally.
        substrate: which canonicalizer to apply to scopes.
        canonicalize_scopes: if False, use raw string scopes (used by
            Tx-NaiveStringScopes ablation to demonstrate canonicalization
            is load-bearing).
    """
    ops_sorted = sorted(ops, key=lambda o: (o.ts, o.tx_id))

    # Last writer / readers seen on each canonical scope.
    last_writer: Dict[str, OpRecord] = {}
    readers_since_write: Dict[str, List[OpRecord]] = defaultdict(list)
    g = ConflictGraph()

    for op in ops_sorted:
        if op.op_kind in {"commit", "abort"}:
            g.nodes.add(op.tx_id)
            continue
        scope_key = (
            canonicalize(op.scope, substrate) if canonicalize_scopes else op.scope
        )
        g.nodes.add(op.tx_id)
        if op.op_kind == "write":
            # write conflicts with previous write & all readers since that write.
            prev_w = last_writer.get(scope_key)
            if prev_w is not None and prev_w.tx_id != op.tx_id:
                g.add_edge(prev_w.tx_id, op.tx_id, (prev_w, op))
            for r in readers_since_write.get(scope_key, []):
                if r.tx_id != op.tx_id:
                    g.add_edge(r.tx_id, op.tx_id, (r, op))
            last_writer[scope_key] = op
            readers_since_write[scope_key] = []
        elif op.op_kind == "read":
            prev_w = last_writer.get(scope_key)
            if prev_w is not None and prev_w.tx_id != op.tx_id:
                g.add_edge(prev_w.tx_id, op.tx_id, (prev_w, op))
            readers_since_write[scope_key].append(op)
        else:
            # Unknown op kind — leave a node but no edge.
            pass

    return g
