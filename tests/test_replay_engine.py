"""Tests for replay engine equality checks."""

from __future__ import annotations

from pathlib import Path

from atomix.replay import ReplayEngine
from atomix.store import SqliteStore


def test_replay_filters_committed_effects(tmp_path: Path) -> None:
    db_path = tmp_path / "atomix.sqlite"
    store = SqliteStore(db_path)

    store.append_effect(
        {
            "tx_id": "tx1",
            "trace_id": "t1",
            "branch_id": "b1",
            "epoch": 0,
            "scopes": ["scope:a"],
            "status": "committed",
            "payload": {"value": 1},
            "idempotency_key": "k1",
        }
    )
    store.append_effect(
        {
            "tx_id": "tx2",
            "trace_id": "t1",
            "branch_id": "b1",
            "epoch": 1,
            "scopes": ["scope:a"],
            "status": "aborted",
            "payload": {"value": 2},
            "idempotency_key": "k2",
        }
    )

    engine = ReplayEngine(store)
    effects = engine.committed_effects(trace_id="t1")

    assert len(effects) == 1
    assert effects[0]["tx_id"] == "tx1"


def test_replay_diff_detects_extra_and_mismatch(tmp_path: Path) -> None:
    db_path = tmp_path / "atomix.sqlite"
    store = SqliteStore(db_path)

    entry_a = {
        "tx_id": "tx1",
        "trace_id": "t1",
        "branch_id": "b1",
        "epoch": 0,
        "scopes": ["scope:a"],
        "status": "committed",
        "payload": {"value": 1},
        "idempotency_key": "k1",
    }
    entry_b = {
        "tx_id": "tx2",
        "trace_id": "t1",
        "branch_id": "b1",
        "epoch": 1,
        "scopes": ["scope:a"],
        "status": "committed",
        "payload": {"value": 2},
        "idempotency_key": "k2",
    }
    store.append_effect(entry_a)
    store.append_effect(entry_b)

    engine = ReplayEngine(store)
    expected = [{**entry_a, "payload": {"value": 999}}]
    diff = engine.compare_committed("t1", expected)

    assert len(diff.extra) == 1
    assert diff.extra[0]["tx_id"] == "tx2"
    assert len(diff.mismatched) == 1
