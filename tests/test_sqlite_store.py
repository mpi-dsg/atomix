"""Tests for the SQLite-backed Atomix store."""

from __future__ import annotations

from pathlib import Path

from atomix.store import SqliteStore
from atomix.tool_result import ArtifactRef


def test_effect_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "atomix.sqlite"
    store = SqliteStore(db_path)

    entry = {
        "tx_id": "tx1",
        "trace_id": "t1",
        "branch_id": "b1",
        "epoch": 3,
        "scopes": ["scope:a"],
        "status": "committed",
        "payload": {"value": 1},
        "idempotency_key": "k1",
    }
    store.append_effect(entry)

    rows = store.list_effects(trace_id="t1")
    assert len(rows) == 1
    assert rows[0]["tx_id"] == "tx1"
    assert rows[0]["payload"] == {"value": 1}


def test_frontier_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "atomix.sqlite"
    store = SqliteStore(db_path)

    store.advance_frontier(trace_id="t1", scope="scope:a", epoch=5)
    store.advance_frontier(trace_id="t1", scope="scope:a", epoch=3)

    assert store.get_frontier(trace_id="t1", scope="scope:a") == 5


def test_artifact_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "atomix.sqlite"
    store = SqliteStore(db_path)

    ref = ArtifactRef.from_bytes("screenshot", b"data", content_type="image/png")
    store.save_artifact(ref)
    loaded = store.get_artifact(ref.sha256)

    assert loaded is not None
    assert loaded.sha256 == ref.sha256
    assert loaded.bytes == ref.bytes


def test_transaction_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "atomix.sqlite"
    store = SqliteStore(db_path)

    entry = {
        "tx_id": "tx1",
        "trace_id": "t1",
        "branch_id": "b1",
        "epoch": 7,
        "status": "pending",
        "scopes": ["scope:a"],
        "reason": None,
    }
    store.save_transaction(entry)
    loaded = store.get_transaction("tx1")

    assert loaded is not None
    assert loaded["tx_id"] == "tx1"
    assert loaded["scopes"] == ["scope:a"]


def test_tool_call_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "atomix.sqlite"
    store = SqliteStore(db_path)

    store.save_tool_call(
        trace_id="t1",
        branch_id="b1",
        tool_name="demo",
        tx_id="tx1",
        epoch=2,
        scopes=["scope:a"],
        tool_input={"key": "value"},
    )
    loaded = store.load_tool_call("t1", "b1", "demo")

    assert loaded is not None
    assert loaded["tx_id"] == "tx1"
    assert loaded["tool_input"] == {"key": "value"}
