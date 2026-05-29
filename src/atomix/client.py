from __future__ import annotations

from typing import Any, Dict, Iterable, Set

from .effects import Effect, ResourceId
from .epoch import Epoch
from .runtime import AtomixRuntime


class AtomixClient:
    """Thin wrapper so external hooks can use AtomixRuntime without importing internals."""

    def __init__(self, runtime: AtomixRuntime) -> None:
        self.runtime = runtime

    def begin(self, scopes: Set[ResourceId], epoch: Epoch):
        return self.runtime.tx_manager.begin(scopes, epoch)

    def record(self, tx, effect: Effect) -> None:
        self.runtime.tx_manager.record_effect(tx, effect)

    def commit(self, tx) -> None:
        self.runtime.tx_manager.commit(tx)

    def abort(self, tx, reason: str) -> None:
        self.runtime.tx_manager.abort(tx, reason)

    def advance(self, scopes: Iterable[ResourceId], epoch: Epoch) -> None:
        self.runtime.advance_frontier(set(scopes), epoch)

    def log_entries(self) -> list[Dict[str, Any]]:
        return self.runtime.log.entries()
