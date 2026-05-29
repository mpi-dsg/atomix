from __future__ import annotations

import json
import uuid
from collections import deque
from pathlib import Path
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Protocol, Set

from .effects import Effect, EffectReversibility, ResourceId, Transaction
from .epoch import Epoch
from .frontier import FrontierTracker

if TYPE_CHECKING:
    from .store import SqliteStore

logger = logging.getLogger(__name__)


def _safe_json_default(obj: Any) -> str:
    """Fallback encoder for non-serializable objects."""
    return f"<unserializable: {type(obj).__name__}>"


class CommitBlocked(Exception):
    """Raised when a transaction cannot commit because frontiers have not advanced."""


class IrreversibleEffectError(Exception):
    """Raised when an irreversible effect is committed without confirmation."""


class EffectAppliedButUnacknowledged(Exception):
    """Raised by adapters after a side effect externalized but acknowledgement failed.

    TransactionManager treats this as applied for idempotency purposes. This is
    the F2 cut point: retrying a non-idempotent external action would duplicate
    the side effect.
    """


class PendingRecoveryError(Exception):
    """Raised when startup finds pending idempotency keys without a recovery policy."""


class EffectLogger(Protocol):
    def append(self, entry: Dict) -> None: ...
    def entries(self) -> List[Dict]: ...


class EffectLog(EffectLogger):
    """Append-only JSONL effect log for replay/debug."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path
        self._entries: List[Dict] = []

    def append(self, entry: Dict) -> None:
        self._entries.append(entry)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=_safe_json_default) + "\n")

    def entries(self) -> List[Dict]:
        return list(self._entries)


class TransactionManager:
    """Manage begin/record/commit/abort with frontier checks and compensations."""

    def __init__(
        self,
        frontier: FrontierTracker,
        apply_effect: Callable[[Effect], None],
        log: EffectLogger | None = None,
        max_idempotency_entries: int | None = 10000,
        frontier_enabled: bool = True,
        store: SqliteStore | None = None,
        recovery_policy: str = "fail_closed",
    ) -> None:
        self.frontier = frontier
        self.apply_effect = apply_effect
        self.log = log or EffectLog()
        self._pending: List[Transaction] = []
        self._pending_ids: Set[str] = set()
        self._applied_idempotency: Set[str] = set()
        self._idempotency_fifo: deque[str] = deque()
        self._max_idempotency_entries = max_idempotency_entries
        self._frontier_enabled = frontier_enabled
        self._store = store
        self._use_persistent_idempotency = store is not None
        self._recovery_policy = recovery_policy
        if self._use_persistent_idempotency:
            self._recover_pending_keys()

    def begin(self, scopes: Set[ResourceId], epoch: Epoch) -> Transaction:
        tx_id = str(uuid.uuid4())
        return Transaction(
            tx_id=tx_id, epoch=epoch, scopes=scopes, effects=[], status="pending"
        )

    def record_effect(self, tx: Transaction, effect: Effect) -> None:
        tx.add_effect(effect)

    def commit(self, tx: Transaction) -> bool:
        if tx.status in {"committed", "aborted"}:
            return True

        self._validate_irreversible_effects(tx)

        if self._frontier_enabled and not self.frontier.can_commit(tx.scopes, tx.epoch):
            tx.status = "waiting"
            if tx.tx_id not in self._pending_ids:
                self._pending.append(tx)
                self._pending_ids.add(tx.tx_id)
            raise CommitBlocked(f"Frontier not ready for tx {tx.tx_id}")
        self._apply(tx)
        tx.status = "committed"
        if tx.tx_id in self._pending_ids:
            self._pending = [
                pending for pending in self._pending if pending.tx_id != tx.tx_id
            ]
            self._pending_ids.discard(tx.tx_id)
        self._log(tx, "committed")
        return True

    def abort(self, tx: Transaction, reason: str) -> None:
        if tx.status == "committed":
            return
        tx.status = "aborted"
        tx.reason = reason
        if tx.tx_id in self._pending_ids:
            self._pending = [
                pending for pending in self._pending if pending.tx_id != tx.tx_id
            ]
            self._pending_ids.discard(tx.tx_id)
        for effect in reversed(tx.effects):
            if effect.compensation and effect.applied:
                try:
                    effect.compensation()
                except Exception:
                    pass  # Best-effort; continue compensating remaining effects
        self._log(tx, "aborted")

    def flush_ready(self) -> List[str]:
        committed: List[str] = []
        still_waiting: List[Transaction] = []
        for tx in self._pending:
            if self.frontier.can_commit(tx.scopes, tx.epoch):
                self._validate_irreversible_effects(tx)
                self._apply(tx)
                tx.status = "committed"
                self._log(tx, "committed")
                committed.append(tx.tx_id)
                self._pending_ids.discard(tx.tx_id)
            else:
                still_waiting.append(tx)
        self._pending = still_waiting
        self._pending_ids = {tx.tx_id for tx in still_waiting}
        return committed

    def abort_branch(self, branch_id: str) -> None:
        """Abort all pending transactions belonging to a branch."""
        for tx in list(self._pending):
            if tx.epoch.branch_id == branch_id:
                self.abort(tx, f"branch {branch_id} aborted")

    def _apply(self, tx: Transaction) -> None:
        applied_this_round: list[Effect] = []
        pending_key_set: set[str] = set()
        try:
            for effect in tx.effects:
                # Check persistent store first if available
                if self._use_persistent_idempotency:
                    if self._store.has_idempotency_key(effect.idempotency_key):
                        continue
                elif effect.idempotency_key in self._applied_idempotency:
                    continue
                if effect.idempotency_key in pending_key_set:
                    continue

                # Phase 1: Mark key as pending BEFORE applying the effect.
                # If we crash after this but before the effect runs, recovery
                # will find the pending key and re-attempt (idempotent retry).
                if self._use_persistent_idempotency:
                    self._store.mark_idempotency_key_pending(
                        effect.idempotency_key, tx.epoch.trace_id, tx.tx_id,
                    )

                # Phase 2: Apply the effect to the outside world.
                try:
                    self.apply_effect(effect)
                except EffectAppliedButUnacknowledged as exc:
                    effect.applied = True
                    if isinstance(effect.payload, dict):
                        effect.payload["post_apply_fault"] = str(exc)
                    applied_this_round.append(effect)
                    pending_key_set.add(effect.idempotency_key)
                    if self._use_persistent_idempotency:
                        self._store.mark_idempotency_key_committed(
                            effect.idempotency_key
                        )
                    else:
                        self._remember_idempotency(effect.idempotency_key)
                    continue
                except Exception:
                    # Effect failed -- delete the pending key we just wrote
                    # so the effect can be retried on next attempt.
                    if self._use_persistent_idempotency:
                        self._store.delete_idempotency_key(effect.idempotency_key)
                    raise
                effect.applied = True
                applied_this_round.append(effect)
                pending_key_set.add(effect.idempotency_key)

                # Phase 3: Mark key as committed AFTER successful application.
                if self._use_persistent_idempotency:
                    self._store.mark_idempotency_key_committed(effect.idempotency_key)
                else:
                    self._remember_idempotency(effect.idempotency_key)
        except Exception:
            # Rollback already-applied effects on failure
            for eff in reversed(applied_this_round):
                if eff.compensation:
                    try:
                        eff.compensation()
                    except Exception:
                        pass  # Best-effort compensation
                eff.applied = False
                # Clean up the committed/pending key so the effect can be retried
                if self._use_persistent_idempotency:
                    self._store.delete_idempotency_key(eff.idempotency_key)
            raise

    def _remember_idempotency(self, key: str) -> None:
        if key in self._applied_idempotency:
            return
        self._applied_idempotency.add(key)
        if self._max_idempotency_entries is None:
            return
        self._idempotency_fifo.append(key)
        if len(self._idempotency_fifo) > self._max_idempotency_entries:
            old = self._idempotency_fifo.popleft()
            self._applied_idempotency.discard(old)

    def _recover_pending_keys(self) -> None:
        """Handle orphaned pending keys from a crash during effect application.

        Strategy: assume effects are idempotent and re-apply them. If the
        effect already took place, the idempotent retry is a no-op. Then mark
        committed so the key blocks future duplicates.

        If no ``apply_effect`` callback can safely re-run (non-idempotent
        effects), callers should override or wrap this method.
        """
        assert self._store is not None
        pending = self._store.get_pending_keys()
        if not pending:
            return
        logger.info("Recovering %d pending idempotency keys", len(pending))
        if self._recovery_policy == "fail_closed":
            keys = ", ".join(key for key, _trace_id, _tx_id in pending[:5])
            raise PendingRecoveryError(
                "Found pending idempotency keys without effect-specific recovery "
                f"policy: {keys}"
            )
        if self._recovery_policy not in {"mark_committed"}:
            raise ValueError(
                "recovery_policy must be 'fail_closed' or 'mark_committed'"
            )
        for key, trace_id, tx_id in pending:
            # Mark as committed -- the effect either happened (crash after
            # apply but before commit) or it didn't (crash before apply).
            # In the latter case the pending key was written but the effect
            # never ran. By marking committed we accept at-most-once for
            # that edge case. For true exactly-once, the caller's
            # apply_effect must be idempotent so a retry is safe.
            #
            # We choose the conservative default: mark committed without
            # re-applying. This avoids accidental side-effects on startup.
            # Callers that need replay-on-recovery can subclass and override.
            logger.info(
                "Marking orphaned pending key as committed: key=%s tx_id=%s",
                key,
                tx_id,
            )
            self._store.mark_idempotency_key_committed(key)

    def _log(self, tx: Transaction, status: str) -> None:
        entry = {
            "tx_id": tx.tx_id,
            "epoch": tx.epoch.value,
            "trace_id": tx.epoch.trace_id,
            "branch_id": tx.epoch.branch_id,
            "scopes": sorted(tx.scopes),
            "effects": tx.effect_descriptions(),
            "status": status,
            "reason": tx.reason,
        }
        entry["effects_payloads"] = [
            {
                "tx_id": tx.tx_id,
                "trace_id": tx.epoch.trace_id,
                "branch_id": tx.epoch.branch_id,
                "epoch": tx.epoch.value,
                "status": status,
                "scopes": sorted(effect.scopes),
                "payload": effect.payload,
                "idempotency_key": effect.idempotency_key,
                "description": effect.description,
            }
            for effect in tx.effects
        ]
        self.log.append(entry)

    @staticmethod
    def _validate_irreversible_effects(tx: Transaction) -> None:
        for effect in tx.effects:
            if (
                effect.reversibility == EffectReversibility.IRREVERSIBLE
                and not effect.confirmed
            ):
                raise IrreversibleEffectError(
                    f"Irreversible effect '{effect.description}' requires confirmation before commit. "
                    f"Set effect.confirmed = True to acknowledge."
                )
