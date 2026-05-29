"""TCC-Confirm baseline (A1): try / confirm / cancel.

Each effect is associated with three callables: try (reserves), confirm
(commits), cancel (releases). The transaction tries each effect; on
commit, all confirms run; on abort, all cancels run (reverse order).

This baseline cancels on the abort sources:
{ tool failure, losing speculation, stale read, pre-commit veto, timeout }

Used in:
- E4 irreversible-effect gating (Tables tab:e4-irrev, tab:e4-abortsources)
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set

from ..effects import Effect, EffectReversibility, ResourceId, Transaction
from ..epoch import Epoch

logger = logging.getLogger(__name__)


@dataclass
class TCCAction:
    """Three-phase action attached to an effect via Effect.payload['tcc']."""

    try_fn: Callable[[], object]
    confirm_fn: Callable[[object], None]
    cancel_fn: Callable[[object], None]


class TCCConfirm:
    """Two-phase try/confirm/cancel.

    Effect.payload may carry a 'tcc' TCCAction. If absent, we fall back to
    Atomix-style apply_effect on commit and compensation on abort, so the
    baseline runs on existing adapters without rework.
    """

    def __init__(
        self,
        apply_effect: Callable[[Effect], None],
        cancel_on: Optional[Set[str]] = None,
    ) -> None:
        self._apply_effect = apply_effect
        self._cancel_on: Set[str] = cancel_on or {
            "tool-failure",
            "losing-speculation",
            "stale-read",
            "pre-commit-veto",
            "timeout",
        }
        self._tries: Dict[str, List[tuple[Effect, Optional[TCCAction], object]]] = (
            defaultdict(list)
        )

    def begin(self, scopes: Set[ResourceId], epoch: Epoch) -> Transaction:
        tx_id = str(uuid.uuid4())
        return Transaction(
            tx_id=tx_id, epoch=epoch, scopes=set(scopes), effects=[], status="pending"
        )

    def record_effect(self, tx: Transaction, effect: Effect) -> None:
        tx.add_effect(effect)
        action = self._extract_tcc(effect)
        try_token = action.try_fn() if action else None
        self._tries[tx.tx_id].append((effect, action, try_token))

    def commit(self, tx: Transaction) -> bool:
        if tx.status in {"committed", "aborted"}:
            return tx.status == "committed"
        # Pre-commit veto for unconfirmed irreversible effects.
        for effect in tx.effects:
            if (
                effect.reversibility == EffectReversibility.IRREVERSIBLE
                and not effect.confirmed
            ):
                self.abort(tx, "pre-commit-veto")
                return False
        try:
            for effect, action, token in self._tries.get(tx.tx_id, []):
                if action is not None:
                    action.confirm_fn(token)
                else:
                    self._apply_effect(effect)
                effect.applied = True
        except Exception as e:
            self.abort(tx, f"tool-failure:{e}")
            raise
        tx.status = "committed"
        self._tries.pop(tx.tx_id, None)
        return True

    def abort(self, tx: Transaction, reason: str) -> None:
        if tx.status == "committed":
            return
        # Cancel only on recognized abort sources (per E4 abort-source matrix).
        # Aborts from unrecognized sources still cancel reservations to keep
        # the baseline safe; the breakdown is for paper reporting only.
        cancel_class = _classify_cancel(reason, self._cancel_on)
        for effect, action, token in reversed(self._tries.get(tx.tx_id, [])):
            if action is not None:
                try:
                    action.cancel_fn(token)
                except Exception:
                    logger.exception("TCC cancel failed")
            elif effect.compensation and effect.applied:
                try:
                    effect.compensation()
                except Exception:
                    logger.exception("Compensation failed")
            effect.applied = False
        tx.status = "aborted"
        tx.reason = f"{reason} (cancel_class={cancel_class})"
        self._tries.pop(tx.tx_id, None)

    @staticmethod
    def _extract_tcc(effect: Effect) -> Optional[TCCAction]:
        if not isinstance(effect.payload, dict):
            return None
        tcc = effect.payload.get("tcc")
        if isinstance(tcc, TCCAction):
            return tcc
        return None


def _classify_cancel(reason: str, recognized: Set[str]) -> str:
    """Map an abort reason to one of the five canonical cancel classes,
    or 'other' if the reason doesn't match. Used for E4 reporting.
    """
    low = reason.lower()
    for cls in recognized:
        if cls in low:
            return cls
    return "other"
