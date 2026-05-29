"""Tests for A4 speculation substrates and runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from atomix.speculation import (
    BufferedSubstrate,
    FilesystemSubstrate,
    MailboxSubstrate,
    SpeculationRunner,
    TauBenchDBSubstrate,
)


def test_buffered_winning_branch_only():
    sub = BufferedSubstrate()
    runner = SpeculationRunner(sub, k=4, seed=42)

    def action(s, bid):
        s.write(bid, "answer", bid)

    results = runner.run(action, winner_index=2)
    won = [r for r in results if r.won]
    aborted = [r for r in results if r.aborted]
    assert len(won) == 1
    assert len(aborted) == 3
    # global_store has exactly the winner's value.
    assert sub.global_store.get("answer") == won[0].branch_id
    # No leftover branches.
    assert sub.branches == {}


def test_filesystem_substrate_residue_only_from_winner(tmp_path: Path):
    sub = FilesystemSubstrate(root=tmp_path / "spec")
    runner = SpeculationRunner(sub, k=3, seed=0)

    def action(s, bid):
        s.write(bid, "out.txt", bid.encode("utf-8"))

    results = runner.run(action, winner_index=1)
    files = sub.commit_dir_files()
    assert len(files) == 1
    winner = next(r for r in results if r.won)
    assert files.pop().read_text() == winner.branch_id


def test_filesystem_substrate_rejects_branch_escape(tmp_path: Path):
    sub = FilesystemSubstrate(root=tmp_path / "spec")

    with pytest.raises(ValueError, match="escapes"):
        sub.write("loser", "../_committed/leak.txt", b"x")

    assert sub.commit_dir_files() == set()


def test_taubench_db_residue_after_abort():
    sub = TauBenchDBSubstrate()
    sub.insert("b1", "orders", "7", {"total": 100})
    sub.insert("b2", "orders", "8", {"total": 200})
    sub.commit("b1")
    sub.abort("b2")
    # b1 winning; b2 aborted should leave no committed residue.
    residue = sub.residue(baseline_branch="b1")
    assert residue == []
    sub.close()


def test_mailbox_aborted_branch_messages_are_residue(tmp_path: Path):
    sub = MailboxSubstrate(log_path=tmp_path / "mail.log")
    sub.send("b1", {"to": "a"})
    sub.send("b2", {"to": "b"})
    # Externalized: cannot un-send. Both messages count as residue if b2 lost.
    sub.commit("b1")
    sub.abort("b2")
    residue = sub.residue(winning_branch_id="b1")
    assert len(residue) == 1
    assert residue[0].payload["to"] == "b"
    sub.close()
