"""Tests for A1 baselines: Mutex+WAL+Rollback, TCC-Confirm, OCC-Revalidate-and-Retry."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from atomix.baselines import MutexWalRollback, OCCRevalidateRetry, TCCConfirm
from atomix.baselines.tcc_confirm import TCCAction
from atomix.effects import Effect, EffectReversibility
from atomix.epoch import Epoch


def _epoch(n: int = 0, trace_id: str = "t0", branch_id: str | None = None) -> Epoch:
    return Epoch(value=n, trace_id=trace_id, branch_id=branch_id)


def _effect(scope: str, payload: dict | None = None) -> Effect:
    return Effect(
        description=f"write {scope}",
        scopes={scope},
        payload=payload or {},
        idempotency_key=f"key-{scope}",
    )


# ---------- MutexWalRollback ----------


class _Recorder:
    def __init__(self):
        self.applied = []

    def __call__(self, effect):
        self.applied.append(effect.description)


def test_mutex_baseline_commits_in_isolation(tmp_path: Path):
    rec = _Recorder()
    bl = MutexWalRollback(rec, wal_path=tmp_path / "wal.jsonl")
    tx = bl.begin({"a"}, _epoch(0))
    bl.record_effect(tx, _effect("a"))
    assert bl.commit(tx)
    assert rec.applied == ["write a"]
    assert tx.status == "committed"
    # WAL has begin, effect, commit.
    lines = (tmp_path / "wal.jsonl").read_text().splitlines()
    assert sum(1 for line in lines if '"op": "begin"' in line) == 1
    assert sum(1 for line in lines if '"op": "commit"' in line) == 1


def test_mutex_baseline_rolls_back_on_apply_failure(tmp_path: Path):
    compensated = []

    def comp():
        compensated.append(1)

    def apply(effect):
        if effect.description == "write b":
            raise RuntimeError("boom")
        # Otherwise, pretend to apply.

    bl = MutexWalRollback(apply, wal_path=tmp_path / "wal.jsonl")
    tx = bl.begin({"a", "b"}, _epoch(0))
    e_a = _effect("a")
    e_a.compensation = comp
    e_b = _effect("b")
    bl.record_effect(tx, e_a)
    bl.record_effect(tx, e_b)
    with pytest.raises(RuntimeError):
        bl.commit(tx)
    # First effect was applied, then compensated.
    assert compensated == [1]
    assert tx.status == "aborted"


def test_mutex_baseline_serializes_overlapping_scopes(tmp_path: Path):
    """Two transactions sharing scope `x` must serialize. We can't deadlock
    because the lock table acquires in sorted order.
    """
    rec = _Recorder()
    bl = MutexWalRollback(rec)
    t1 = bl.begin({"x", "y"}, _epoch(0))
    # A second begin on overlapping scope should block; but we test the
    # release path via a controlled commit-then-second-begin flow.
    bl.record_effect(t1, _effect("x"))
    bl.commit(t1)
    t2 = bl.begin({"x"}, _epoch(1))
    bl.record_effect(t2, _effect("x"))
    bl.commit(t2)
    assert rec.applied == ["write x", "write x"]


# ---------- TCCConfirm ----------


def test_tcc_basic_apply_path():
    rec = _Recorder()
    bl = TCCConfirm(rec)
    tx = bl.begin({"a"}, _epoch(0))
    bl.record_effect(tx, _effect("a"))
    assert bl.commit(tx)
    assert rec.applied == ["write a"]


def test_tcc_three_phase_uses_try_confirm_cancel():
    confirms, cancels, tries = [], [], []

    def apply_fallback(effect):
        # Should NOT be called when TCC action is provided.
        raise AssertionError("fallback not expected")

    bl = TCCConfirm(apply_fallback)
    tcc = TCCAction(
        try_fn=lambda: tries.append(1) or "tok",
        confirm_fn=lambda tok: confirms.append(tok),
        cancel_fn=lambda tok: cancels.append(tok),
    )
    tx = bl.begin({"a"}, _epoch(0))
    bl.record_effect(tx, _effect("a", {"tcc": tcc}))
    bl.commit(tx)
    assert tries == [1]
    assert confirms == ["tok"]
    assert cancels == []


def test_tcc_cancels_on_abort():
    confirms, cancels = [], []
    bl = TCCConfirm(lambda e: None)
    tcc = TCCAction(
        try_fn=lambda: "tok",
        confirm_fn=lambda tok: confirms.append(tok),
        cancel_fn=lambda tok: cancels.append(tok),
    )
    tx = bl.begin({"a"}, _epoch(0))
    bl.record_effect(tx, _effect("a", {"tcc": tcc}))
    bl.abort(tx, "tool-failure")
    assert confirms == []
    assert cancels == ["tok"]


def test_tcc_pre_commit_veto_on_unconfirmed_irreversible():
    bl = TCCConfirm(lambda e: None)
    tx = bl.begin({"send_email"}, _epoch(0))
    e = _effect("send_email")
    e.reversibility = EffectReversibility.IRREVERSIBLE
    e.confirmed = False
    bl.record_effect(tx, e)
    assert bl.commit(tx) is False
    assert tx.status == "aborted"
    assert "pre-commit-veto" in tx.reason


# ---------- OCCRevalidateRetry ----------


def test_occ_commits_when_no_concurrent_writer():
    rec = _Recorder()
    bl = OCCRevalidateRetry(rec, retry_budget=3)
    tx = bl.begin({"a"}, _epoch(0))
    bl.record_effect(tx, _effect("a"))
    assert bl.commit(tx)
    assert rec.applied == ["write a"]


def test_occ_aborts_on_stale_read():
    """tx1 reads `a`. tx2 commits a write to `a`. tx1 then tries to commit
    a write to `b`; OCC must detect tx1's read-set is stale.

    Within budget the tx stays retryable (status='pending'); only after
    budget exhaustion does the tx become permanently aborted.
    """
    rec = _Recorder()
    bl = OCCRevalidateRetry(rec, retry_budget=3)
    tx1 = bl.begin({"a"}, _epoch(0))
    tx2 = bl.begin({"a"}, _epoch(1))
    bl.record_effect(tx2, _effect("a"))
    bl.commit(tx2)
    bl.record_effect(tx1, _effect("b"))
    result = bl.commit(tx1)
    assert result is False
    assert tx1.status == "pending"  # retryable
    assert "stale-read" in tx1.reason
    # On retry with no further changes, the read stamps are now current and
    # the commit succeeds.
    assert bl.commit(tx1) is True
    assert tx1.status == "committed"


def test_occ_retry_budget_exhausts():
    rec = _Recorder()
    bl = OCCRevalidateRetry(rec, retry_budget=1)
    tx = bl.begin({"x"}, _epoch(0))
    # Race: another tx bumps version twice.
    other1 = bl.begin({"x"}, _epoch(1))
    bl.record_effect(other1, _effect("x"))
    bl.commit(other1)
    bl.record_effect(tx, _effect("y"))
    # First commit attempt: stale-read.
    bl.commit(tx)
    other2 = bl.begin({"x"}, _epoch(2))
    bl.record_effect(other2, _effect("x"))
    bl.commit(other2)
    bl.commit(tx)  # second attempt: still stale, > budget
    assert "retry-budget-exhausted" in tx.reason


def test_occ_concurrent_writers_do_not_both_commit():
    rec = _Recorder()

    def slow_apply(effect):
        time.sleep(0.05)
        rec(effect)

    bl = OCCRevalidateRetry(slow_apply, retry_budget=3)
    tx1 = bl.begin({"x"}, _epoch(0))
    tx2 = bl.begin({"x"}, _epoch(1))
    bl.record_effect(tx1, _effect("x"))
    bl.record_effect(tx2, _effect("x"))
    start = threading.Barrier(2)
    results = []

    def commit(tx):
        start.wait(timeout=1)
        results.append(bl.commit(tx))

    t1 = threading.Thread(target=commit, args=(tx1,))
    t2 = threading.Thread(target=commit, args=(tx2,))
    t1.start()
    t2.start()
    t1.join(timeout=1)
    t2.join(timeout=1)

    assert not t1.is_alive()
    assert not t2.is_alive()
    assert results.count(True) == 1
    assert results.count(False) == 1
    assert rec.applied == ["write x"]
