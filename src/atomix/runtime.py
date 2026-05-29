from __future__ import annotations

import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict

from .adapters import ToolAdapter
from .effects import Effect, ResourceId, Transaction
from .epoch import Epoch, EpochManager
from .frontier import FrontierTracker
from .injector import FaultInjector, FaultProfile
from .registry import AdapterRegistry
from .transactions import CommitBlocked, EffectLog, TransactionManager
from .tool_result import ToolMeta, normalize_tool_result
from .store import SqliteStore, StoreEffectLog


def _maybe_flush(tx_manager) -> None:
    """Call ``flush_ready`` on the tx manager if it has one.

    Atomix's ``TransactionManager`` defers commits whose frontier hasn't
    advanced; ``flush_ready`` resolves them. The mechanism baselines
    (Mutex+WAL+Rollback, TCC-Confirm, OCC-Revalidate-and-Retry) commit
    synchronously and have no deferred queue, so they don't implement
    ``flush_ready``. This helper makes the runtime polymorphic over both.
    """
    flush = getattr(tx_manager, "flush_ready", None)
    if callable(flush):
        flush()


class AtomixRuntime:
    """Coordinates adapters, transactions, frontiers, and fault injection."""

    def __init__(
        self,
        apply_effect: Callable[[Effect], None],
        effect_log_path: str | None = "logs/effects.jsonl",
        fault_profile: FaultProfile | None = None,
        store: SqliteStore | None = None,
        frontier_enabled: bool = True,
        recovery_policy: str = "fail_closed",
    ) -> None:
        self.frontier = FrontierTracker()
        self._frontier_enabled = frontier_enabled
        self.epochs = EpochManager()
        self.injector = FaultInjector(fault_profile)
        if store:
            self.log = StoreEffectLog(store)
        else:
            self.log = (
                EffectLog(Path(effect_log_path)) if effect_log_path else EffectLog()
            )
        self.tx_manager = TransactionManager(
            self.frontier,
            apply_effect,
            self.log,
            frontier_enabled=frontier_enabled,
            store=store,
            recovery_policy=recovery_policy,
        )
        self.adapters = AdapterRegistry()
        self.store = store
        self._auto_frontier_lock = threading.RLock()
        self._active_epochs: dict[tuple[str, str | None, ResourceId], set[int]] = (
            defaultdict(set)
        )
        self._finalized_epochs: dict[tuple[str, str | None, ResourceId], set[int]] = (
            defaultdict(set)
        )

    def register_adapter(self, name: str, adapter: ToolAdapter) -> None:
        self.adapters.register(name, adapter)

    def run_tool(
        self,
        tool_name: str,
        tool_fn: Callable[..., Any],
        args: Dict[str, Any],
        epoch: Epoch,
        *,
        auto_advance: bool | None = None,
    ) -> tuple[Any, Transaction]:
        adapter = self.adapters.get(tool_name)
        scopes = adapter.scopes(args)
        should_auto_advance = self._should_auto_advance(epoch, auto_advance)
        if should_auto_advance:
            self._register_active_epoch(scopes, epoch)
        tx = self.tx_manager.begin(scopes, epoch)
        result: Any | None = None
        try:
            result = self.injector.call(lambda: tool_fn(**args))

            meta = ToolMeta(
                tool_name=tool_name,
                trace_id=epoch.trace_id,
                branch_id=epoch.branch_id,
                attempt=0,
            )
            tool_result = normalize_tool_result(result, meta=meta)
            effect = adapter.to_effect(args, tool_result, epoch)
            self.tx_manager.record_effect(tx, effect)
            if should_auto_advance:
                self._finalize_auto_epoch(scopes, epoch)
            self.tx_manager.commit(tx)
            if should_auto_advance:
                _maybe_flush(self.tx_manager)
            return result, tx
        except CommitBlocked:
            # commit will happen when frontiers advance
            return result, tx
        except Exception as exc:
            self.tx_manager.abort(tx, reason=str(exc))
            if should_auto_advance:
                self._finalize_auto_epoch(scopes, epoch)
                _maybe_flush(self.tx_manager)
            raise

    def advance_frontier(self, scopes: set[ResourceId], epoch: Epoch) -> None:
        self.frontier.advance(scopes, epoch)
        _maybe_flush(self.tx_manager)

    def abort_branch(self, branch_id: str) -> None:
        self.tx_manager.abort_branch(branch_id)

    @staticmethod
    def _should_auto_advance(epoch: Epoch, auto_advance: bool | None) -> bool:
        if auto_advance is not None:
            return auto_advance
        return epoch.branch_id is None

    def _register_active_epoch(self, scopes: set[ResourceId], epoch: Epoch) -> None:
        with self._auto_frontier_lock:
            for scope in scopes:
                self._active_epochs[self._epoch_scope_key(scope, epoch)].add(
                    epoch.value
                )

    def _finalize_auto_epoch(self, scopes: set[ResourceId], epoch: Epoch) -> None:
        with self._auto_frontier_lock:
            for scope in scopes:
                key = self._epoch_scope_key(scope, epoch)
                active = self._active_epochs[key]
                active.discard(epoch.value)
                finalized = self._finalized_epochs[key]
                finalized.add(epoch.value)
                target = self._advanceable_epoch(active, finalized)
                if target is None:
                    continue
                self.frontier.advance(
                    {scope},
                    Epoch(target, trace_id=epoch.trace_id, branch_id=epoch.branch_id),
                )
                finalized.difference_update(
                    [value for value in finalized if value <= target]
                )

    @staticmethod
    def _epoch_scope_key(
        scope: ResourceId, epoch: Epoch
    ) -> tuple[str, str | None, ResourceId]:
        return (epoch.trace_id, epoch.branch_id, scope)

    @staticmethod
    def _advanceable_epoch(
        active: set[int], finalized: set[int]
    ) -> int | None:
        for candidate in sorted(finalized, reverse=True):
            if not any(value < candidate for value in active):
                return candidate
        return None
