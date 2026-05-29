from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, List, Sequence, Set

from .epoch import Epoch


ResourceId = str
Compensation = Callable[[], None]


class EffectReversibility(Enum):
    """Classification of effect reversibility."""

    REVERSIBLE = "reversible"  # Full undo possible (file writes)
    REVERSIBLE_WITH_COST = "with_cost"  # Undo possible but has cost (refunds, cancellations)
    IRREVERSIBLE = "irreversible"  # Cannot undo (sent emails, financial transfers)


@dataclass
class Effect:
    """Represents a tool side-effect with scope metadata."""

    description: str
    scopes: Set[ResourceId]
    payload: Any
    idempotency_key: str
    compensation: Compensation | None = None
    applied: bool = False
    reversibility: EffectReversibility = EffectReversibility.REVERSIBLE
    confirmed: bool = False  # For irreversible effects


@dataclass
class Transaction:
    """In-memory transaction state tracked by the manager."""

    tx_id: str
    epoch: Epoch
    scopes: Set[ResourceId]
    effects: List[Effect]
    status: str  # pending | committed | aborted | waiting
    reason: str | None = None

    def add_effect(self, effect: Effect) -> None:
        self.effects.append(effect)

    def effect_descriptions(self) -> Sequence[str]:
        return [effect.description for effect in self.effects]
