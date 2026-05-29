"""OCC-Revalidate-and-Retry baseline (A1).

Mechanism:
- Reads stamp the (scope, version) seen.
- Writes bump the version on commit.
- On commit, every read's stamp is revalidated; if any read's version is
  stale, the transaction aborts and is retried, up to a budget.
- When the budget is exhausted, the transaction is permanently aborted
  (work is lost).

Used in:
- E1 clean-success (Table tab:e1-clean), as a baseline that retries on
  staleness but has no frontier.
- E2 multi-agent (Tables tab:e2-multiagent).
- E8 granularity (Table tab:e8-granularity), as the OCC reference.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Set

from ..effects import Effect, EffectReversibility, ResourceId, Transaction
from ..epoch import Epoch

logger = logging.getLogger(__name__)


@dataclass
class _ReadStamp:
    scope: ResourceId
    version_seen: int


@dataclass
class _TxState:
    read_set: List[_ReadStamp] = field(default_factory=list)
    write_set: Set[ResourceId] = field(default_factory=set)
    attempts: int = 0


class OCCRevalidateRetry:
    """OCC with revalidate-and-retry on stale reads.

    Args:
        apply_effect: same surface as Atomix's TransactionManager.
        retry_budget: max retries before permanent abort (default 3).
    """

    def __init__(
        self,
        apply_effect: Callable[[Effect], None],
        retry_budget: int = 3,
    ) -> None:
        self._apply_effect = apply_effect
        self._retry_budget = retry_budget
        self._versions: Dict[ResourceId, int] = defaultdict(int)
        self._versions_lock = threading.RLock()
        self._states: Dict[str, _TxState] = {}

    # --- BaselineProtocol ---

    def begin(self, scopes: Set[ResourceId], epoch: Epoch) -> Transaction:
        tx_id = str(uuid.uuid4())
        self._states[tx_id] = _TxState()
        # Stamp reads at begin: caller declares its read scope.
        with self._versions_lock:
            for scope in scopes:
                self._states[tx_id].read_set.append(
                    _ReadStamp(scope=scope, version_seen=self._versions[scope])
                )
        return Transaction(
            tx_id=tx_id, epoch=epoch, scopes=set(scopes), effects=[], status="pending"
        )

    def record_effect(self, tx: Transaction, effect: Effect) -> None:
        tx.add_effect(effect)
        st = self._states[tx.tx_id]
        for s in effect.scopes:
            st.write_set.add(s)

    def commit(self, tx: Transaction) -> bool:
        if tx.status in {"committed", "aborted"}:
            return tx.status == "committed"
        st = self._states[tx.tx_id]
        # Pre-commit irreversibility check (mirrors Atomix for fair comparison).
        for effect in tx.effects:
            if (
                effect.reversibility == EffectReversibility.IRREVERSIBLE
                and not effect.confirmed
            ):
                self.abort(tx, "pre-commit-veto")
                return False
        with self._versions_lock:
            if not self._revalidate_unlocked(st):
                st.attempts += 1
                # Re-stamp the read set with current versions so the harness can
                # retry by calling commit again. Budget governs how many retries.
                st.read_set = [
                    _ReadStamp(scope=stamp.scope, version_seen=self._versions[stamp.scope])
                    for stamp in st.read_set
                ]
                if st.attempts > self._retry_budget:
                    self.abort(tx, f"retry-budget-exhausted({st.attempts})")
                    return False
                # Mark this attempt aborted but keep status retryable: the caller
                # decides whether to re-issue commit (which will revalidate again).
                tx.reason = f"stale-read(retry={st.attempts})"
                tx.status = "pending"
                return False

            # Apply writes while holding the validation lock so no peer can
            # validate against the old versions and commit concurrently.
            applied: List[Effect] = []
            try:
                for effect in tx.effects:
                    self._apply_effect(effect)
                    effect.applied = True
                    applied.append(effect)
            except Exception:
                for eff in reversed(applied):
                    if eff.compensation:
                        try:
                            eff.compensation()
                        except Exception:
                            logger.exception("Compensation failed")
                    eff.applied = False
                self.abort(tx, "tool-failure")
                raise
            for scope in st.write_set:
                self._versions[scope] += 1
        tx.status = "committed"
        self._states.pop(tx.tx_id, None)
        return True

    def abort(self, tx: Transaction, reason: str) -> None:
        if tx.status == "committed":
            return
        # Compensate any applied effects (defensive — usually none on OCC abort).
        for effect in reversed(tx.effects):
            if effect.applied and effect.compensation:
                try:
                    effect.compensation()
                except Exception:
                    logger.exception("Compensation failed")
            effect.applied = False
        tx.status = "aborted"
        tx.reason = reason
        # Keep state for retry counting; cleared once the harness re-issues a tx.

    # --- accessors used by harness retry logic ---

    def attempts(self, tx_id: str) -> int:
        return self._states.get(tx_id, _TxState()).attempts

    def retry_budget(self) -> int:
        return self._retry_budget

    # --- internals ---

    def _revalidate(self, st: _TxState) -> bool:
        with self._versions_lock:
            return self._revalidate_unlocked(st)

    def _revalidate_unlocked(self, st: _TxState) -> bool:
        for stamp in st.read_set:
            current = self._versions[stamp.scope]
            if current != stamp.version_seen:
                return False
        return True
