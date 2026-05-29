from __future__ import annotations

from pathlib import Path

from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.frontier import FrontierTracker
from atomix.transactions import CommitBlocked, EffectLog, TransactionManager


def test_frontier_blocks_commit_until_advanced(tmp_path: Path) -> None:
    applied: list[str] = []

    def apply(effect: Effect) -> None:
        applied.append(effect.payload["value"])

    frontier = FrontierTracker()
    log = EffectLog()
    manager = TransactionManager(frontier, apply, log)
    epoch = Epoch(0, trace_id="t1")
    tx = manager.begin({"res"}, epoch)
    eff = Effect(description="e1", scopes={"res"}, payload={"value": "v1"}, idempotency_key="k1")
    manager.record_effect(tx, eff)

    try:
        manager.commit(tx)
    except CommitBlocked:
        pass

    assert applied == []
    assert tx.status == "waiting"

    frontier.advance({"res"}, epoch)
    manager.flush_ready()

    assert applied == ["v1"]
    assert tx.status == "committed"


def test_abort_branch_removes_pending(tmp_path: Path) -> None:
    frontier = FrontierTracker()
    applied: list[str] = []

    def apply(effect: Effect) -> None:
        applied.append(effect.payload["value"])

    manager = TransactionManager(frontier, apply, EffectLog())

    epoch_a = Epoch(0, "trace", branch_id="A")
    epoch_b = Epoch(0, "trace", branch_id="B")

    tx_a = manager.begin({"res"}, epoch_a)
    eff_a = Effect(description="a", scopes={"res"}, payload={"value": "a"}, idempotency_key="ka")
    manager.record_effect(tx_a, eff_a)
    try:
        manager.commit(tx_a)
    except CommitBlocked:
        pass

    tx_b = manager.begin({"res"}, epoch_b)
    eff_b = Effect(description="b", scopes={"res"}, payload={"value": "b"}, idempotency_key="kb")
    manager.record_effect(tx_b, eff_b)
    try:
        manager.commit(tx_b)
    except CommitBlocked:
        pass

    assert len(manager._pending) == 2

    manager.abort_branch("B")
    assert len(manager._pending) == 1

    frontier.advance({"res"}, epoch_a)
    manager.flush_ready()

    assert applied == ["a"]
