"""Mailbox speculation substrate.

Wraps `atomix.sinks.AppendOnlyLog`. Branch writes are addressed by
`branch_id`. The substrate enforces the invariant: only the winning
branch's messages should ever reach the sink. Residue: log entries whose
branch_id is not the winning branch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ...sinks.append_only_log import AppendOnlyLog, LogRecord


@dataclass
class MailboxSubstrate:
    """Append-only mailbox indexed by branch.

    Every branch write goes to the underlying log immediately to model
    "externalized" effects (the mailbox cannot un-send). Atomix should
    refuse to write here from a losing branch in the first place; if it
    does, the residue check will catch it.
    """

    log_path: Path
    _log: AppendOnlyLog = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._log = AppendOnlyLog(Path(self.log_path))

    def open_branch(self, branch_id: str) -> None:
        # No-op.
        pass

    def send(self, branch_id: str, payload: dict) -> LogRecord:
        body = dict(payload)
        body["_branch_id"] = branch_id
        return self._log.append(body)

    def commit(self, branch_id: str) -> None:
        # Externalized: nothing to do. The writes already escaped.
        pass

    def abort(self, branch_id: str) -> None:
        # Cannot un-send. The harness must refuse to send here from
        # losing branches. If we got here with messages tagged this
        # branch_id, those count as residue.
        pass

    def residue(self, winning_branch_id: Optional[str]) -> List[LogRecord]:
        out: List[LogRecord] = []
        for rec in self._log:
            br = rec.payload.get("_branch_id")
            if br is None:
                continue
            if winning_branch_id is None or br != winning_branch_id:
                out.append(rec)
        return out

    def close(self) -> None:
        self._log.close()
