"""Comprehensive tests for TransactionManager."""

from __future__ import annotations

from typing import List

import pytest

from atomix.effects import Effect, EffectReversibility
from atomix.epoch import Epoch
from atomix.frontier import FrontierTracker
from atomix.transactions import (
    CommitBlocked,
    EffectLog,
    IrreversibleEffectError,
    TransactionManager,
)


class TestTransactionLifecycle:
    """Tests for basic transaction lifecycle operations."""

    @pytest.fixture
    def setup(self) -> tuple[TransactionManager, FrontierTracker, list[Effect]]:
        """Create a TransactionManager with tracking."""
        applied: List[Effect] = []
        frontier = FrontierTracker()
        log = EffectLog()
        manager = TransactionManager(frontier, lambda e: applied.append(e), log)
        return manager, frontier, applied

    def test_begin_creates_pending_transaction(
        self, setup: tuple[TransactionManager, FrontierTracker, list[Effect]]
    ) -> None:
        """begin() should create a transaction with pending status."""
        manager, frontier, applied = setup
        epoch = Epoch(0, trace_id="t1")
        tx = manager.begin({"res"}, epoch)
        assert tx.status == "pending"
        assert tx.epoch == epoch
        assert tx.scopes == {"res"}
        assert tx.effects == []

    def test_record_effect_adds_to_transaction(
        self, setup: tuple[TransactionManager, FrontierTracker, list[Effect]]
    ) -> None:
        """record_effect() should add effect to transaction."""
        manager, frontier, applied = setup
        tx = manager.begin({"res"}, Epoch(0, trace_id="t1"))
        effect = Effect(
            description="test",
            scopes={"res"},
            payload={"value": 1},
            idempotency_key="k1",
        )
        manager.record_effect(tx, effect)
        assert len(tx.effects) == 1
        assert tx.effects[0] == effect

    def test_commit_with_ready_frontier(
        self, setup: tuple[TransactionManager, FrontierTracker, list[Effect]]
    ) -> None:
        """commit() should succeed when frontier is ready."""
        manager, frontier, applied = setup
        epoch = Epoch(0, trace_id="t1")
        frontier.advance({"res"}, epoch)

        tx = manager.begin({"res"}, epoch)
        effect = Effect(
            description="test",
            scopes={"res"},
            payload={"value": "committed"},
            idempotency_key="k1",
        )
        manager.record_effect(tx, effect)
        result = manager.commit(tx)

        assert result is True
        assert tx.status == "committed"
        assert len(applied) == 1
        assert applied[0].payload == {"value": "committed"}

    def test_commit_blocked_without_frontier(
        self, setup: tuple[TransactionManager, FrontierTracker, list[Effect]]
    ) -> None:
        """commit() should raise CommitBlocked when frontier not ready."""
        manager, frontier, applied = setup
        epoch = Epoch(0, trace_id="t1")

        tx = manager.begin({"res"}, epoch)
        effect = Effect(
            description="test",
            scopes={"res"},
            payload={"value": "blocked"},
            idempotency_key="k1",
        )
        manager.record_effect(tx, effect)

        with pytest.raises(CommitBlocked):
            manager.commit(tx)

        assert tx.status == "waiting"
        assert len(applied) == 0

    def test_commit_does_not_duplicate_pending(
        self, setup: tuple[TransactionManager, FrontierTracker, list[Effect]]
    ) -> None:
        """Repeated commit attempts should not duplicate pending entries."""
        manager, frontier, applied = setup
        epoch = Epoch(0, trace_id="t1")

        tx = manager.begin({"res"}, epoch)
        effect = Effect(
            description="test",
            scopes={"res"},
            payload={"value": "blocked"},
            idempotency_key="k1",
        )
        manager.record_effect(tx, effect)

        with pytest.raises(CommitBlocked):
            manager.commit(tx)
        with pytest.raises(CommitBlocked):
            manager.commit(tx)

        assert len(manager._pending) == 1

        frontier.advance({"res"}, epoch)
        manager.flush_ready()

        assert len(applied) == 1

    def test_commit_clears_pending_when_frontier_ready(self) -> None:
        """commit() should remove a waiting tx from pending when it commits."""
        applied: List[Effect] = []
        frontier = FrontierTracker()
        manager = TransactionManager(frontier, lambda e: applied.append(e))

        epoch = Epoch(0, trace_id="t1")
        tx = manager.begin({"res"}, epoch)
        effect = Effect(
            description="test",
            scopes={"res"},
            payload={},
            idempotency_key="k1",
        )
        manager.record_effect(tx, effect)

        with pytest.raises(CommitBlocked):
            manager.commit(tx)

        frontier.advance({"res"}, epoch)
        manager.commit(tx)

        assert tx.status == "committed"
        assert len(manager._pending) == 0

    def test_flush_ready_revalidates_irreversible_confirmation(self) -> None:
        applied: List[Effect] = []
        frontier = FrontierTracker()
        manager = TransactionManager(frontier, lambda e: applied.append(e))
        epoch = Epoch(0, trace_id="t1")
        tx = manager.begin({"res"}, epoch)

        with pytest.raises(CommitBlocked):
            manager.commit(tx)

        manager.record_effect(
            tx,
            Effect(
                description="send-mail",
                scopes={"res"},
                payload={},
                idempotency_key="mail-1",
                reversibility=EffectReversibility.IRREVERSIBLE,
                confirmed=False,
            ),
        )
        frontier.advance({"res"}, epoch)

        with pytest.raises(IrreversibleEffectError):
            manager.flush_ready()
        assert applied == []

    def test_commit_idempotent_after_committed(
        self, setup: tuple[TransactionManager, FrontierTracker, list[Effect]]
    ) -> None:
        """commit() on already committed transaction should return True."""
        manager, frontier, applied = setup
        epoch = Epoch(0, trace_id="t1")
        frontier.advance({"res"}, epoch)

        tx = manager.begin({"res"}, epoch)
        manager.commit(tx)
        assert tx.status == "committed"

        # Second commit should be no-op
        result = manager.commit(tx)
        assert result is True

    def test_commit_idempotent_after_aborted(
        self, setup: tuple[TransactionManager, FrontierTracker, list[Effect]]
    ) -> None:
        """commit() on aborted transaction should return True (no-op)."""
        manager, frontier, applied = setup
        epoch = Epoch(0, trace_id="t1")

        tx = manager.begin({"res"}, epoch)
        manager.abort(tx, "test abort")
        assert tx.status == "aborted"

        # Commit on aborted should be no-op
        result = manager.commit(tx)
        assert result is True


class TestTransactionAbort:
    """Tests for transaction abort and compensation."""

    def test_abort_runs_compensations_in_reverse(self) -> None:
        """abort() should run compensations in reverse order."""
        compensation_order: List[str] = []
        applied: List[Effect] = []
        frontier = FrontierTracker()
        manager = TransactionManager(frontier, lambda e: applied.append(e))

        epoch = Epoch(0, trace_id="t1")
        frontier.advance({"res"}, epoch)
        tx = manager.begin({"res"}, epoch)

        # Record multiple effects with compensations
        for i in range(3):
            def make_comp(idx: int):
                return lambda: compensation_order.append(f"comp_{idx}")

            effect = Effect(
                description=f"effect_{i}",
                scopes={"res"},
                payload={"idx": i},
                idempotency_key=f"k{i}",
                compensation=make_comp(i),
            )
            manager.record_effect(tx, effect)

        # Commit to apply effects
        manager.commit(tx)
        assert all(e.applied for e in tx.effects)

        # Manually reset status to test abort with applied effects
        tx.status = "pending"
        manager.abort(tx, "rollback")

        # Compensations should run in reverse (2, 1, 0)
        assert compensation_order == ["comp_2", "comp_1", "comp_0"]

    def test_abort_skips_unapplied_effects(self) -> None:
        """abort() should not run compensations for unapplied effects."""
        compensation_ran = []
        frontier = FrontierTracker()
        manager = TransactionManager(frontier, lambda e: None)

        epoch = Epoch(0, trace_id="t1")
        tx = manager.begin({"res"}, epoch)

        effect = Effect(
            description="test",
            scopes={"res"},
            payload={},
            idempotency_key="k1",
            compensation=lambda: compensation_ran.append(True),
        )
        manager.record_effect(tx, effect)

        # Abort without committing (effect.applied is False)
        manager.abort(tx, "cancel")
        assert compensation_ran == []

    def test_abort_removes_from_pending(self) -> None:
        """abort() should remove transaction from pending queue."""
        frontier = FrontierTracker()
        manager = TransactionManager(frontier, lambda e: None)

        epoch = Epoch(0, trace_id="t1")
        tx = manager.begin({"res"}, epoch)

        try:
            manager.commit(tx)
        except CommitBlocked:
            pass

        assert len(manager._pending) == 1
        manager.abort(tx, "cancel")
        assert len(manager._pending) == 0

    def test_abort_on_committed_is_noop(self) -> None:
        """abort() on committed transaction should do nothing."""
        compensation_ran = []
        frontier = FrontierTracker()
        manager = TransactionManager(frontier, lambda e: None)

        epoch = Epoch(0, trace_id="t1")
        frontier.advance({"res"}, epoch)
        tx = manager.begin({"res"}, epoch)

        effect = Effect(
            description="test",
            scopes={"res"},
            payload={},
            idempotency_key="k1",
            compensation=lambda: compensation_ran.append(True),
        )
        manager.record_effect(tx, effect)
        manager.commit(tx)

        # Try to abort committed transaction
        manager.abort(tx, "too late")
        assert tx.status == "committed"  # Status unchanged
        assert compensation_ran == []  # No compensation ran


class TestFlushReady:
    """Tests for flush_ready() behavior."""

    def test_flush_commits_ready_transactions(self) -> None:
        """flush_ready() should commit transactions whose frontiers are ready."""
        applied: List[str] = []
        frontier = FrontierTracker()
        manager = TransactionManager(
            frontier, lambda e: applied.append(e.idempotency_key)
        )

        # Create waiting transactions
        for i in range(3):
            epoch = Epoch(i, trace_id="t1")
            tx = manager.begin({f"res_{i}"}, epoch)
            effect = Effect(
                description=f"e{i}",
                scopes={f"res_{i}"},
                payload={},
                idempotency_key=f"k{i}",
            )
            manager.record_effect(tx, effect)
            try:
                manager.commit(tx)
            except CommitBlocked:
                pass

        assert len(manager._pending) == 3

        # Advance frontiers for first two
        frontier.advance({"res_0"}, Epoch(0, trace_id="t1"))
        frontier.advance({"res_1"}, Epoch(1, trace_id="t1"))

        committed = manager.flush_ready()
        assert len(committed) == 2
        assert len(manager._pending) == 1  # res_2 still waiting
        assert "k0" in applied
        assert "k1" in applied
        assert "k2" not in applied

    def test_flush_returns_committed_tx_ids(self) -> None:
        """flush_ready() should return list of committed transaction IDs."""
        frontier = FrontierTracker()
        manager = TransactionManager(frontier, lambda e: None)

        epoch = Epoch(0, trace_id="t1")
        tx = manager.begin({"res"}, epoch)
        effect = Effect(
            description="test",
            scopes={"res"},
            payload={},
            idempotency_key="k1",
        )
        manager.record_effect(tx, effect)

        try:
            manager.commit(tx)
        except CommitBlocked:
            pass

        frontier.advance({"res"}, epoch)
        committed = manager.flush_ready()

        assert committed == [tx.tx_id]

    def test_flush_empty_when_nothing_ready(self) -> None:
        """flush_ready() should return empty list when nothing is ready."""
        frontier = FrontierTracker()
        manager = TransactionManager(frontier, lambda e: None)

        epoch = Epoch(0, trace_id="t1")
        tx = manager.begin({"res"}, epoch)
        try:
            manager.commit(tx)
        except CommitBlocked:
            pass

        committed = manager.flush_ready()
        assert committed == []


class TestAbortBranch:
    """Tests for branch-level abort operations."""

    def test_abort_branch_removes_all_branch_transactions(self) -> None:
        """abort_branch() should abort all transactions for a branch."""
        frontier = FrontierTracker()
        manager = TransactionManager(frontier, lambda e: None)

        # Create transactions for different branches
        tx_a1 = manager.begin({"res"}, Epoch(0, "trace", branch_id="A"))
        tx_a2 = manager.begin({"res"}, Epoch(1, "trace", branch_id="A"))
        tx_b1 = manager.begin({"res"}, Epoch(0, "trace", branch_id="B"))

        for tx in [tx_a1, tx_a2, tx_b1]:
            try:
                manager.commit(tx)
            except CommitBlocked:
                pass

        assert len(manager._pending) == 3

        manager.abort_branch("A")
        assert len(manager._pending) == 1
        assert manager._pending[0].epoch.branch_id == "B"

    def test_abort_branch_runs_compensations(self) -> None:
        """abort_branch() should run compensations for applied effects."""
        compensated: List[str] = []
        frontier = FrontierTracker()
        manager = TransactionManager(frontier, lambda e: None)

        epoch = Epoch(0, "trace", branch_id="losing")
        frontier.advance({"res"}, epoch)

        tx = manager.begin({"res"}, epoch)
        effect = Effect(
            description="test",
            scopes={"res"},
            payload={},
            idempotency_key="k1",
            compensation=lambda: compensated.append("compensated"),
        )
        manager.record_effect(tx, effect)
        manager.commit(tx)

        # Manually reset to test branch abort
        tx.status = "pending"
        manager._pending.append(tx)

        manager.abort_branch("losing")
        assert "compensated" in compensated

    def test_abort_nonexistent_branch_is_noop(self) -> None:
        """abort_branch() for non-existent branch should do nothing."""
        frontier = FrontierTracker()
        manager = TransactionManager(frontier, lambda e: None)

        tx = manager.begin({"res"}, Epoch(0, "trace", branch_id="A"))
        try:
            manager.commit(tx)
        except CommitBlocked:
            pass

        # Abort non-existent branch
        manager.abort_branch("nonexistent")
        assert len(manager._pending) == 1  # A still there


class TestEffectLogging:
    """Tests for transaction logging behavior."""

    def test_commit_logs_entry(self) -> None:
        """Committed transactions should be logged."""
        frontier = FrontierTracker()
        log = EffectLog()
        manager = TransactionManager(frontier, lambda e: None, log)

        epoch = Epoch(5, trace_id="trace123", branch_id="branch_x")
        frontier.advance({"res"}, epoch)

        tx = manager.begin({"res"}, epoch)
        effect = Effect(
            description="write:file.txt",
            scopes={"res"},
            payload={},
            idempotency_key="k1",
        )
        manager.record_effect(tx, effect)
        manager.commit(tx)

        entries = log.entries()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["tx_id"] == tx.tx_id
        assert entry["epoch"] == 5
        assert entry["trace_id"] == "trace123"
        assert entry["branch_id"] == "branch_x"
        assert entry["status"] == "committed"
        assert entry["effects"] == ["write:file.txt"]

    def test_commit_logs_effect_payloads(self) -> None:
        """Committed entries should include effect payload metadata."""
        frontier = FrontierTracker()
        log = EffectLog()
        manager = TransactionManager(frontier, lambda e: None, log)

        epoch = Epoch(1, trace_id="trace_payload")
        frontier.advance({"res"}, epoch)

        tx = manager.begin({"res"}, epoch)
        effect = Effect(
            description="write:file.txt",
            scopes={"res"},
            payload={"path": "file.txt", "content": "hello"},
            idempotency_key="idem-1",
        )
        manager.record_effect(tx, effect)
        manager.commit(tx)

        entry = log.entries()[0]
        payloads = entry.get("effects_payloads")
        assert payloads is not None
        assert payloads[0]["payload"] == {"path": "file.txt", "content": "hello"}
        assert payloads[0]["idempotency_key"] == "idem-1"

    def test_abort_logs_entry_with_reason(self) -> None:
        """Aborted transactions should be logged with reason."""
        frontier = FrontierTracker()
        log = EffectLog()
        manager = TransactionManager(frontier, lambda e: None, log)

        tx = manager.begin({"res"}, Epoch(0, trace_id="t1"))
        manager.abort(tx, "test failure reason")

        entries = log.entries()
        assert len(entries) == 1
        assert entries[0]["status"] == "aborted"
        assert entries[0]["reason"] == "test failure reason"


class TestIdempotency:
    """Tests for idempotency enforcement."""

    def test_idempotency_prevents_double_apply(self) -> None:
        applied: List[str] = []
        frontier = FrontierTracker()
        manager = TransactionManager(frontier, lambda e: applied.append(e.idempotency_key))

        frontier.advance({"res"}, Epoch(1, trace_id="t1"))

        tx1 = manager.begin({"res"}, Epoch(0, trace_id="t1"))
        effect1 = Effect(
            description="dup",
            scopes={"res"},
            payload={},
            idempotency_key="dup",
        )
        manager.record_effect(tx1, effect1)
        manager.commit(tx1)

        tx2 = manager.begin({"res"}, Epoch(1, trace_id="t1"))
        effect2 = Effect(
            description="dup",
            scopes={"res"},
            payload={},
            idempotency_key="dup",
        )
        manager.record_effect(tx2, effect2)
        manager.commit(tx2)

        assert applied == ["dup"]
