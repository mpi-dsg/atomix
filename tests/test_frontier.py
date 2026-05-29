"""Comprehensive tests for FrontierTracker."""

from __future__ import annotations


from atomix.epoch import Epoch
from atomix.frontier import FrontierTracker


class TestFrontierTracker:
    """Tests for FrontierTracker commit gating and advancement."""

    def test_empty_frontier_blocks_any_positive_epoch(self) -> None:
        """Fresh frontier should block commits at epoch > -1."""
        tracker = FrontierTracker()
        epoch = Epoch(0, trace_id="t1")
        assert not tracker.can_commit({"resource"}, epoch)

    def test_empty_frontier_allows_negative_epoch(self) -> None:
        """Edge case: epoch -1 would be allowed (frontier defaults to -1)."""
        tracker = FrontierTracker()
        # This is a degenerate case - epochs should start at 0
        # Testing that frontier_for returns -1 by default
        assert tracker.frontier_for("any") == -1

    def test_advance_enables_commit(self) -> None:
        """After advancing, commit at that epoch should succeed."""
        tracker = FrontierTracker()
        epoch = Epoch(5, trace_id="t1")
        tracker.advance({"res"}, epoch)
        assert tracker.can_commit({"res"}, epoch)

    def test_advance_enables_earlier_epochs(self) -> None:
        """Advancing to epoch N should allow commits at epochs <= N."""
        tracker = FrontierTracker()
        tracker.advance({"res"}, Epoch(10, trace_id="t1"))
        assert tracker.can_commit({"res"}, Epoch(5, trace_id="t1"))
        assert tracker.can_commit({"res"}, Epoch(0, trace_id="t1"))
        assert tracker.can_commit({"res"}, Epoch(10, trace_id="t1"))

    def test_advance_blocks_future_epochs(self) -> None:
        """Advancing to epoch N should still block epochs > N."""
        tracker = FrontierTracker()
        tracker.advance({"res"}, Epoch(5, trace_id="t1"))
        assert not tracker.can_commit({"res"}, Epoch(6, trace_id="t1"))
        assert not tracker.can_commit({"res"}, Epoch(100, trace_id="t1"))

    def test_multiple_scopes_all_must_be_ready(self) -> None:
        """can_commit requires ALL scopes to have sufficient frontier."""
        tracker = FrontierTracker()
        epoch = Epoch(5, trace_id="t1")
        tracker.advance({"res_a"}, epoch)
        # Only res_a advanced, res_b is still at -1
        assert not tracker.can_commit({"res_a", "res_b"}, epoch)
        # Advance res_b too
        tracker.advance({"res_b"}, epoch)
        assert tracker.can_commit({"res_a", "res_b"}, epoch)

    def test_independent_resource_frontiers(self) -> None:
        """Each resource tracks its frontier independently."""
        tracker = FrontierTracker()
        tracker.advance({"res_a"}, Epoch(10, trace_id="t1"))
        tracker.advance({"res_b"}, Epoch(5, trace_id="t1"))
        assert tracker.frontier_for("res_a") == 10
        assert tracker.frontier_for("res_b") == 5
        assert tracker.frontier_for("res_c") == -1

    def test_frontier_scoped_by_trace(self) -> None:
        """Frontiers should not leak across traces."""
        tracker = FrontierTracker()
        tracker.advance({"res"}, Epoch(5, trace_id="t1"))
        assert tracker.can_commit({"res"}, Epoch(5, trace_id="t1"))
        assert not tracker.can_commit({"res"}, Epoch(5, trace_id="t2"))
        assert tracker.frontier_for("res", trace_id="t1") == 5
        assert tracker.frontier_for("res", trace_id="t2") == -1

    def test_advance_is_monotonic(self) -> None:
        """Advancing to a lower epoch should not regress the frontier."""
        tracker = FrontierTracker()
        tracker.advance({"res"}, Epoch(10, trace_id="t1"))
        tracker.advance({"res"}, Epoch(5, trace_id="t1"))  # Lower epoch
        assert tracker.frontier_for("res") == 10  # Should stay at 10

    def test_advance_multiple_scopes_at_once(self) -> None:
        """advance() should handle multiple scopes in one call."""
        tracker = FrontierTracker()
        epoch = Epoch(7, trace_id="t1")
        tracker.advance({"a", "b", "c"}, epoch)
        assert tracker.frontier_for("a") == 7
        assert tracker.frontier_for("b") == 7
        assert tracker.frontier_for("c") == 7

    def test_empty_scopes_always_can_commit(self) -> None:
        """Empty scope set should always allow commit."""
        tracker = FrontierTracker()
        epoch = Epoch(100, trace_id="t1")
        assert tracker.can_commit(set(), epoch)


class TestBranchTracking:
    """Tests for speculative branch management."""

    def test_open_branch(self) -> None:
        """open_branch adds branch to tracked set."""
        tracker = FrontierTracker()
        tracker.open_branch("branch_a")
        assert "branch_a" in tracker.branches()

    def test_close_branch(self) -> None:
        """close_branch removes branch from tracked set."""
        tracker = FrontierTracker()
        tracker.open_branch("branch_a")
        tracker.close_branch("branch_a")
        assert "branch_a" not in tracker.branches()

    def test_close_nonexistent_branch_is_noop(self) -> None:
        """Closing a branch that was never opened should not raise."""
        tracker = FrontierTracker()
        tracker.close_branch("nonexistent")  # Should not raise

    def test_multiple_branches(self) -> None:
        """Multiple branches can be tracked simultaneously."""
        tracker = FrontierTracker()
        tracker.open_branch("a")
        tracker.open_branch("b")
        tracker.open_branch("c")
        assert tracker.branches() == {"a", "b", "c"}

    def test_branches_returns_copy(self) -> None:
        """branches() should return a copy, not the internal set."""
        tracker = FrontierTracker()
        tracker.open_branch("a")
        branches = tracker.branches()
        branches.add("external")  # Modify returned set
        assert "external" not in tracker.branches()  # Internal set unchanged
