"""Tests for persistent idempotency via SQLite."""

from pathlib import Path
import pytest
from atomix.store import SqliteStore
from atomix.effects import Effect
from atomix.transactions import (
    EffectAppliedButUnacknowledged,
    PendingRecoveryError,
    TransactionManager,
)
from atomix.frontier import FrontierTracker
from atomix.epoch import Epoch


def test_idempotency_survives_beyond_memory_limit(tmp_path: Path) -> None:
    """Verify that with SQLite store, idempotency works beyond 10k limit."""
    store = SqliteStore(tmp_path / "test.sqlite")
    applied_count = 0

    def apply_effect(effect):
        nonlocal applied_count
        applied_count += 1

    frontier = FrontierTracker()
    tm = TransactionManager(
        frontier,
        apply_effect,
        store=store,
    )

    # Run 15k effects with repeating keys (more than 10k limit)
    for i in range(15000):
        key_id = i % 5000  # Only 5000 unique keys, each repeated 3x
        epoch = Epoch(value=i, trace_id="test")
        tx = tm.begin({"scope"}, epoch)
        effect = Effect(
            description=f"effect_{key_id}",
            scopes={"scope"},
            payload={},
            idempotency_key=f"key_{key_id}",
        )
        tm.record_effect(tx, effect)
        frontier.advance({"scope"}, epoch)
        tm.commit(tx)

    # Should have applied only 5000 unique effects, not 15000
    assert applied_count == 5000
    store.close()


def test_idempotency_persists_across_runtime_restart(tmp_path: Path) -> None:
    """Verify idempotency keys survive runtime restart."""
    db_path = tmp_path / "test.sqlite"
    applied_keys = []

    def apply_effect(effect):
        applied_keys.append(effect.idempotency_key)

    # First runtime instance
    store1 = SqliteStore(db_path)
    frontier1 = FrontierTracker()
    tm1 = TransactionManager(frontier1, apply_effect, store=store1)

    epoch1 = Epoch(value=1, trace_id="test")
    tx1 = tm1.begin({"scope"}, epoch1)
    effect1 = Effect(
        description="write_file",
        scopes={"scope"},
        payload={"path": "/tmp/x.txt", "content": "a"},
        idempotency_key="file_write_x_a",
    )
    tm1.record_effect(tx1, effect1)
    frontier1.advance({"scope"}, epoch1)
    tm1.commit(tx1)

    assert applied_keys == ["file_write_x_a"]
    store1.close()

    # Second runtime instance (simulating restart)
    store2 = SqliteStore(db_path)
    frontier2 = FrontierTracker()
    tm2 = TransactionManager(frontier2, apply_effect, store=store2)

    # Try to apply same effect again - should be deduplicated
    epoch2 = Epoch(value=2, trace_id="test")
    tx2 = tm2.begin({"scope"}, epoch2)
    effect2 = Effect(
        description="write_file",
        scopes={"scope"},
        payload={"path": "/tmp/x.txt", "content": "a"},
        idempotency_key="file_write_x_a",  # Same key
    )
    tm2.record_effect(tx2, effect2)
    frontier2.advance({"scope"}, epoch2)
    tm2.commit(tx2)

    # Should still be 1 (deduplicated across restart)
    assert len(applied_keys) == 1
    store2.close()


def test_memory_idempotency_without_store(tmp_path: Path) -> None:
    """Verify in-memory idempotency still works without store."""
    applied_count = 0

    def apply_effect(effect):
        nonlocal applied_count
        applied_count += 1

    frontier = FrontierTracker()
    tm = TransactionManager(
        frontier,
        apply_effect,
        store=None,  # No persistent store
        max_idempotency_entries=100,  # Small limit for testing
    )

    # Apply 50 effects with same keys twice
    for round in range(2):
        for i in range(50):
            epoch = Epoch(value=round * 50 + i, trace_id="test")
            tx = tm.begin({"scope"}, epoch)
            effect = Effect(
                description=f"effect_{i}",
                scopes={"scope"},
                payload={},
                idempotency_key=f"key_{i}",
            )
            tm.record_effect(tx, effect)
            frontier.advance({"scope"}, epoch)
            tm.commit(tx)

    # Should have applied only 50 unique effects
    assert applied_count == 50


def test_store_idempotency_methods(tmp_path: Path) -> None:
    """Test SqliteStore idempotency methods directly."""
    store = SqliteStore(tmp_path / "test.sqlite")

    # Key should not exist initially
    assert not store.has_idempotency_key("key1")

    # Mark the key
    store.mark_idempotency_key("key1", "trace1", "tx1")

    # Key should exist now
    assert store.has_idempotency_key("key1")

    # Marking again should be idempotent (INSERT OR REPLACE)
    store.mark_idempotency_key("key1", "trace2", "tx2")
    assert store.has_idempotency_key("key1")

    # Different key should not exist
    assert not store.has_idempotency_key("key2")

    store.close()


# ---------------------------------------------------------------------------
# Crash-safe two-phase idempotency tests
# ---------------------------------------------------------------------------


def test_pending_to_committed_normal_flow(tmp_path: Path) -> None:
    """Key goes pending -> committed during normal effect application."""
    store = SqliteStore(tmp_path / "test.sqlite")
    applied: list[str] = []

    def apply_effect(effect: Effect) -> None:
        applied.append(effect.idempotency_key)

    frontier = FrontierTracker()
    tm = TransactionManager(frontier, apply_effect, store=store)

    epoch = Epoch(value=0, trace_id="t1")
    tx = tm.begin({"s"}, epoch)
    eff = Effect(
        description="write",
        scopes={"s"},
        payload={},
        idempotency_key="k1",
    )
    tm.record_effect(tx, eff)
    frontier.advance({"s"}, epoch)
    tm.commit(tx)

    # Effect was applied exactly once
    assert applied == ["k1"]
    # Key is committed in the store
    assert store.has_idempotency_key("k1")
    # No pending keys remain
    assert store.get_pending_keys() == []
    store.close()


def test_crash_leaves_pending_key_recovery_fails_closed_by_default(tmp_path: Path) -> None:
    """Simulate crash: key stays pending; default recovery stops for operator handling."""
    db_path = tmp_path / "test.sqlite"
    store = SqliteStore(db_path)

    # Manually insert a pending key to simulate a crash mid-apply
    store.mark_idempotency_key_pending("orphan_key", "trace1", "tx_crash")
    assert store.get_pending_keys() == [("orphan_key", "trace1", "tx_crash")]
    # Pending key should NOT appear as committed
    assert not store.has_idempotency_key("orphan_key")
    store.close()

    # Reopen the store and create a new TransactionManager -- recovery fails closed.
    store2 = SqliteStore(db_path)
    applied: list[str] = []

    def apply_effect(effect: Effect) -> None:
        applied.append(effect.idempotency_key)

    frontier = FrontierTracker()
    with pytest.raises(PendingRecoveryError, match="pending idempotency"):
        TransactionManager(frontier, apply_effect, store=store2)

    assert not store2.has_idempotency_key("orphan_key")
    assert store2.get_pending_keys() == [("orphan_key", "trace1", "tx_crash")]
    store2.close()


def test_legacy_mark_committed_recovery_policy_skips_retry(tmp_path: Path) -> None:
    """Legacy recovery policy remains available for explicit at-most-once deployments."""
    db_path = tmp_path / "test.sqlite"
    store = SqliteStore(db_path)
    store.mark_idempotency_key_pending("orphan_key", "trace1", "tx_crash")
    store.close()

    store2 = SqliteStore(db_path)
    applied: list[str] = []

    def apply_effect(effect: Effect) -> None:
        applied.append(effect.idempotency_key)

    # If we now try to apply an effect with the same key, it should be skipped
    frontier = FrontierTracker()
    tm = TransactionManager(
        frontier, apply_effect, store=store2, recovery_policy="mark_committed"
    )
    epoch = Epoch(value=0, trace_id="trace1")
    tx = tm.begin({"s"}, epoch)
    eff = Effect(
        description="same effect",
        scopes={"s"},
        payload={},
        idempotency_key="orphan_key",
    )
    tm.record_effect(tx, eff)
    frontier.advance({"s"}, epoch)
    tm.commit(tx)

    assert applied == []
    store2.close()


def test_pending_key_requires_recovery_policy(tmp_path: Path) -> None:
    """A pending key requires explicit recovery policy before normal retry."""
    db_path = tmp_path / "test.sqlite"
    store = SqliteStore(db_path)

    # Insert a pending key (simulates crash before commit)
    store.mark_idempotency_key_pending("retry_key", "trace1", "tx1")
    # has_idempotency_key must return False for pending keys
    assert not store.has_idempotency_key("retry_key")
    store.close()

    store2 = SqliteStore(db_path)
    applied: list[str] = []

    def apply_effect(effect: Effect) -> None:
        applied.append(effect.idempotency_key)

    frontier = FrontierTracker()
    with pytest.raises(PendingRecoveryError):
        TransactionManager(frontier, apply_effect, store=store2)

    assert not store2.has_idempotency_key("retry_key")
    store2.close()


def test_pending_key_without_recovery_allows_reapply(tmp_path: Path) -> None:
    """Without TransactionManager recovery, pending keys allow re-application."""
    db_path = tmp_path / "test.sqlite"
    store = SqliteStore(db_path)

    # Insert pending key directly
    store.mark_idempotency_key_pending("pk", "t1", "tx1")
    # Pending key does not count as committed
    assert not store.has_idempotency_key("pk")
    # So a fresh check would not block the effect
    store.close()


def test_failed_effect_cleans_up_pending_key(tmp_path: Path) -> None:
    """If apply_effect raises, the pending key is deleted so retry is possible."""
    store = SqliteStore(tmp_path / "test.sqlite")
    call_count = 0

    def failing_apply(effect: Effect) -> None:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("network error")

    frontier = FrontierTracker()
    tm = TransactionManager(frontier, failing_apply, store=store)

    epoch = Epoch(value=0, trace_id="t1")
    tx = tm.begin({"s"}, epoch)
    eff = Effect(
        description="api call",
        scopes={"s"},
        payload={},
        idempotency_key="fail_key",
    )
    tm.record_effect(tx, eff)
    frontier.advance({"s"}, epoch)

    with pytest.raises(RuntimeError, match="network error"):
        tm.commit(tx)

    # The pending key should have been cleaned up
    assert not store.has_idempotency_key("fail_key")
    assert store.get_pending_keys() == []

    # Retry should be possible
    call_count = 0
    applied: list[str] = []

    def good_apply(effect: Effect) -> None:
        applied.append(effect.idempotency_key)

    tm2 = TransactionManager(frontier, good_apply, store=store)
    epoch2 = Epoch(value=1, trace_id="t1")
    tx2 = tm2.begin({"s"}, epoch2)
    eff2 = Effect(
        description="api call retry",
        scopes={"s"},
        payload={},
        idempotency_key="fail_key",
    )
    tm2.record_effect(tx2, eff2)
    frontier.advance({"s"}, epoch2)
    tm2.commit(tx2)

    assert applied == ["fail_key"]
    assert store.has_idempotency_key("fail_key")
    store.close()


def test_post_apply_fault_marks_key_committed_without_retry(tmp_path: Path) -> None:
    """F2 post-apply faults must not be retried like pre-apply failures."""
    store = SqliteStore(tmp_path / "test.sqlite")
    applied: list[str] = []

    def apply_then_lost_ack(effect: Effect) -> None:
        applied.append(effect.idempotency_key)
        raise EffectAppliedButUnacknowledged("lost ack")

    frontier = FrontierTracker()
    tm = TransactionManager(frontier, apply_then_lost_ack, store=store)
    epoch = Epoch(value=0, trace_id="t1")
    tx = tm.begin({"s"}, epoch)
    effect = Effect(
        description="external action",
        scopes={"s"},
        payload={},
        idempotency_key="f2-key",
    )
    tm.record_effect(tx, effect)
    frontier.advance({"s"}, epoch)
    tm.commit(tx)
    tm.commit(tx)

    assert applied == ["f2-key"]
    assert store.has_idempotency_key("f2-key")
    assert effect.payload["post_apply_fault"] == "lost ack"
    store.close()


def test_schema_migration_adds_status_column(tmp_path: Path) -> None:
    """Opening an old DB without status column should auto-migrate."""
    import sqlite3

    db_path = tmp_path / "old.sqlite"
    # Create a DB with the old schema (no status column)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key TEXT PRIMARY KEY,
            trace_id TEXT,
            tx_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT INTO idempotency_keys (key, trace_id, tx_id) VALUES ('old_key', 't1', 'tx1')"
    )
    conn.commit()
    conn.close()

    # Open with SqliteStore -- migration should add the status column
    store = SqliteStore(db_path)
    # Old key should be treated as committed (default value)
    assert store.has_idempotency_key("old_key")
    # New two-phase methods should work
    store.mark_idempotency_key_pending("new_key", "t2", "tx2")
    assert not store.has_idempotency_key("new_key")
    store.mark_idempotency_key_committed("new_key")
    assert store.has_idempotency_key("new_key")
    store.close()


def test_multiple_pending_keys_fail_closed_by_default(tmp_path: Path) -> None:
    """Multiple orphaned pending keys are left pending without explicit policy."""
    db_path = tmp_path / "test.sqlite"
    store = SqliteStore(db_path)
    for i in range(5):
        store.mark_idempotency_key_pending(f"key_{i}", "trace1", f"tx_{i}")
    assert len(store.get_pending_keys()) == 5
    store.close()

    # Default recovery stops so an operator/effect-specific replay can decide.
    store2 = SqliteStore(db_path)
    applied: list[str] = []

    def apply_effect(effect: Effect) -> None:
        applied.append(effect.idempotency_key)

    frontier = FrontierTracker()
    with pytest.raises(PendingRecoveryError):
        TransactionManager(frontier, apply_effect, store=store2)

    assert len(store2.get_pending_keys()) == 5
    for i in range(5):
        assert not store2.has_idempotency_key(f"key_{i}")
    store2.close()
