"""
State tracking for OSWorld UI interactions.

Captures screenshots and UI state for debugging and potential rollback.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class UIStateSnapshot:
    """Snapshot of UI state at a point in time."""

    timestamp: datetime
    screenshot_hash: str
    screenshot_data: Optional[bytes]
    active_window: str
    active_app: str
    epoch_value: int
    action_description: str
    action_type: str
    action_args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StateTracker:
    """Tracks UI state changes for debugging and potential rollback."""

    snapshots: List[UIStateSnapshot] = field(default_factory=list)
    max_snapshots: int = 100
    store_screenshots: bool = True  # Whether to store actual screenshot data

    def record(
        self,
        screenshot: Optional[bytes],
        active_window: str,
        active_app: str,
        epoch_value: int,
        action_description: str,
        action_type: str = "",
        action_args: Optional[Dict[str, Any]] = None,
    ) -> UIStateSnapshot:
        """Record a state snapshot."""
        screenshot_hash = ""
        if screenshot:
            screenshot_hash = hashlib.sha256(screenshot).hexdigest()[:16]

        snapshot = UIStateSnapshot(
            timestamp=datetime.now(),
            screenshot_hash=screenshot_hash,
            screenshot_data=screenshot if self.store_screenshots else None,
            active_window=active_window,
            active_app=active_app,
            epoch_value=epoch_value,
            action_description=action_description,
            action_type=action_type,
            action_args=action_args or {},
        )
        self.snapshots.append(snapshot)

        # Trim old snapshots
        if len(self.snapshots) > self.max_snapshots:
            self.snapshots = self.snapshots[-self.max_snapshots :]

        return snapshot

    def get_snapshot_at_epoch(self, epoch_value: int) -> Optional[UIStateSnapshot]:
        """Get the snapshot recorded at a specific epoch."""
        for snapshot in reversed(self.snapshots):
            if snapshot.epoch_value == epoch_value:
                return snapshot
        return None

    def get_snapshot_before_epoch(self, epoch_value: int) -> Optional[UIStateSnapshot]:
        """Get the snapshot immediately before a given epoch."""
        prev_snapshot = None
        for snapshot in self.snapshots:
            if snapshot.epoch_value >= epoch_value:
                return prev_snapshot
            prev_snapshot = snapshot
        return prev_snapshot

    def get_snapshots_since_epoch(self, epoch_value: int) -> List[UIStateSnapshot]:
        """Get all snapshots since a given epoch (inclusive)."""
        return [s for s in self.snapshots if s.epoch_value >= epoch_value]

    def get_snapshots_in_range(
        self, start_epoch: int, end_epoch: int
    ) -> List[UIStateSnapshot]:
        """Get snapshots in an epoch range (inclusive)."""
        return [
            s for s in self.snapshots if start_epoch <= s.epoch_value <= end_epoch
        ]

    def get_recent_snapshots(self, n: int) -> List[UIStateSnapshot]:
        """Get the N most recent snapshots."""
        return self.snapshots[-n:] if n > 0 else []

    def get_action_history(self) -> List[Dict[str, Any]]:
        """Get action history without screenshot data."""
        return [
            {
                "timestamp": s.timestamp.isoformat(),
                "step": s.epoch_value,
                "epoch": s.epoch_value,
                "action": s.action_description,
                "action_type": s.action_type,
                "app": s.active_app,
                "window": s.active_window,
            }
            for s in self.snapshots
        ]

    def clear(self) -> None:
        """Clear all snapshots."""
        self.snapshots.clear()

    def detect_state_change(
        self, before_screenshot: Optional[bytes], after_screenshot: Optional[bytes]
    ) -> bool:
        """Detect if screenshots differ (indicating state change)."""
        if not before_screenshot or not after_screenshot:
            return True  # Assume change if we can't compare

        before_hash = hashlib.sha256(before_screenshot).hexdigest()
        after_hash = hashlib.sha256(after_screenshot).hexdigest()

        return before_hash != after_hash

    def __len__(self) -> int:
        return len(self.snapshots)
