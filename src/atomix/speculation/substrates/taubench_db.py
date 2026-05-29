"""τ-bench DB speculation substrate.

Per-run sqlite DB with a `branches` table tracking inserted rows by
branch. On commit, the winning branch's rows are kept; on abort, they
are deleted. Residue: rows tagged with an aborted branch_id.

Used for E3 mailbox/db class. Defaults to in-memory sqlite so tests are
hermetic; pass a path to persist for paper runs.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class TauBenchDBSubstrate:
    db_path: Optional[Path] = None
    _conn: sqlite3.Connection = field(init=False)
    _lock: threading.RLock = field(init=False, default_factory=threading.RLock)

    def __post_init__(self) -> None:
        target = str(self.db_path) if self.db_path else ":memory:"
        self._conn = sqlite3.connect(target, isolation_level=None, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS rows ("
            "  table_name TEXT NOT NULL,"
            "  pk TEXT NOT NULL,"
            "  branch_id TEXT NOT NULL,"
            "  status TEXT NOT NULL,"  # 'pending' | 'committed' | 'aborted'
            "  payload TEXT NOT NULL,"
            "  PRIMARY KEY (table_name, pk, branch_id)"
            ")"
        )

    def open_branch(self, branch_id: str) -> None:
        # No-op; rows reference branch_id directly.
        pass

    def insert(self, branch_id: str, table: str, pk: str, payload: dict) -> None:
        import json

        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO rows (table_name, pk, branch_id, status, payload) "
                "VALUES (?, ?, ?, 'pending', ?)",
                (table, pk, branch_id, json.dumps(payload, sort_keys=True)),
            )

    def commit(self, branch_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE rows SET status='committed' "
                "WHERE branch_id=? AND status='pending'",
                (branch_id,),
            )
            return cur.rowcount

    def abort(self, branch_id: str) -> int:
        """Hard-delete pending rows for the branch; tag committed rows as residue."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM rows WHERE branch_id=? AND status='pending'",
                (branch_id,),
            )
            return cur.rowcount

    def residue(self, baseline_branch: str = "") -> List[Tuple[str, str, str, dict]]:
        """Committed rows whose branch_id is NOT the winning branch."""
        import json

        with self._lock:
            cur = self._conn.execute(
                "SELECT table_name, pk, branch_id, payload FROM rows "
                "WHERE status='committed' AND branch_id != ?",
                (baseline_branch,),
            )
            return [
                (row[0], row[1], row[2], json.loads(row[3]))
                for row in cur.fetchall()
            ]

    def close(self) -> None:
        self._conn.close()
