"""Common protocol implemented by every mechanism baseline.

The protocol is a subset of `TransactionManager` so harnesses can pivot
between Atomix and a baseline by swapping a single field.
"""

from __future__ import annotations

from typing import Protocol, Set

from ..effects import Effect, ResourceId, Transaction
from ..epoch import Epoch


class BaselineProtocol(Protocol):
    """Minimal interface a mechanism baseline must support.

    A harness drives this in the order `begin -> record_effect... -> commit`
    or aborts with `abort(tx, reason)`.
    """

    def begin(self, scopes: Set[ResourceId], epoch: Epoch) -> Transaction: ...

    def record_effect(self, tx: Transaction, effect: Effect) -> None: ...

    def commit(self, tx: Transaction) -> bool: ...

    def abort(self, tx: Transaction, reason: str) -> None: ...
