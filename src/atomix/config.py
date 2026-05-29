"""Atomix configuration and ablation flags.

Centralizes the ablation flags used by the tracked evaluation configs under
``configs/experiments/``. Each flag disables a specific Atomix mechanism so
the evaluation can show it is load-bearing.

Flag names match the paper exactly. Do not rename without updating both
the paper and `tests/test_ablations.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AblationFlags:
    """Toggles individual Atomix mechanisms for ablation experiments.

    All flags default to False (full Atomix). Set a flag to True to disable
    the corresponding mechanism. The `name` returns the canonical identifier
    used in paper tables (e.g., "Tx-Full", "Tx-NoScopeOnRead", ...).
    """

    no_scope_on_read: bool = False
    """Tx-NoScopeOnRead: skip scope-registration call on first read.

    Paper: Table tab:e2-ablations.
    Effect: stale-plan reads are not tracked, so conflicts on the read set
    are missed. Expected to produce serializability violations under
    forced-overlap multi-agent.
    """

    no_abort_on_stale: bool = False
    """Tx-NoAbortOnStale: never abort on stale plan; wait forever.

    Paper: Table tab:e2-ablations.
    Effect: livelock under contention. Tail latency explodes; throughput
    collapses on forced-overlap.
    """

    global_frontier: bool = False
    """Tx-GlobalFrontier: replace per-resource frontier dict with single scalar.

    Paper: Tables tab:e2-ablations, tab:e3-speculation, tab:e8-granularity.
    Effect: any committed write blocks every other resource, eliminating
    parallelism between disjoint scopes.
    """

    misclassified_irreversible: bool = False
    """Atomix-MisclassifiedIrreversible: register irreversible tools as reversible.

    Paper: Tables tab:e3-speculation, tab:e4-irrev.
    Effect: speculation/aborts are allowed to run on tools whose effects
    cannot be compensated. Expected nonzero leak in mailbox/sink classes.
    """

    naive_string_scopes: bool = False
    """Tx-NaiveStringScopes: disable canonicalization in scope matcher.

    Paper: Table tab:e6-aliasing.
    Effect: aliased scopes (symlinks, alternative IDs, wildcards) are not
    recognized as the same resource. Conflict graph misses cycles.
    """

    no_residue_classification: bool = False
    """Tx-Full-NoResidueClassification: count all aborts as clean.

    Paper: Table tab:e7-compfail.
    Effect: residue from compensation failures is misreported as clean
    success. Used to demonstrate the residue ledger is load-bearing.
    """

    @property
    def name(self) -> str:
        """Canonical identifier used in paper tables."""
        if not any(
            (
                self.no_scope_on_read,
                self.no_abort_on_stale,
                self.global_frontier,
                self.misclassified_irreversible,
                self.naive_string_scopes,
                self.no_residue_classification,
            )
        ):
            return "Tx-Full"
        # Each flag has a single canonical name; multiple flags compose with '+'.
        parts = []
        if self.no_scope_on_read:
            parts.append("Tx-NoScopeOnRead")
        if self.no_abort_on_stale:
            parts.append("Tx-NoAbortOnStale")
        if self.global_frontier:
            parts.append("Tx-GlobalFrontier")
        if self.misclassified_irreversible:
            parts.append("Atomix-MisclassifiedIrreversible")
        if self.naive_string_scopes:
            parts.append("Tx-NaiveStringScopes")
        if self.no_residue_classification:
            parts.append("Tx-Full-NoResidueClassification")
        return "+".join(parts)


def parse_flags(spec: Optional[str]) -> AblationFlags:
    """Parse a `+`-separated flag spec into AblationFlags.

    >>> parse_flags("Tx-Full").name
    'Tx-Full'
    >>> parse_flags("Tx-GlobalFrontier").global_frontier
    True
    >>> parse_flags("Tx-NoScopeOnRead+Tx-GlobalFrontier").no_scope_on_read
    True
    """
    if spec is None or spec.strip() in {"", "Tx-Full"}:
        return AblationFlags()
    parts = {p.strip() for p in spec.split("+") if p.strip()}
    known = {
        "Tx-NoScopeOnRead",
        "Tx-NoAbortOnStale",
        "Tx-GlobalFrontier",
        "Atomix-MisclassifiedIrreversible",
        "Tx-NaiveStringScopes",
        "Tx-Full-NoResidueClassification",
    }
    unknown = sorted(parts - known)
    if unknown:
        raise ValueError(f"Unknown Atomix ablation flag(s): {', '.join(unknown)}")
    return AblationFlags(
        no_scope_on_read="Tx-NoScopeOnRead" in parts,
        no_abort_on_stale="Tx-NoAbortOnStale" in parts,
        global_frontier="Tx-GlobalFrontier" in parts,
        misclassified_irreversible="Atomix-MisclassifiedIrreversible" in parts,
        naive_string_scopes="Tx-NaiveStringScopes" in parts,
        no_residue_classification="Tx-Full-NoResidueClassification" in parts,
    )
