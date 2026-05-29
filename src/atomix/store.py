"""SQLite store for Atomix state and logs."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .tool_result import ArtifactRef


class StoreEffectLog:
    def __init__(self, store: "SqliteStore") -> None:
        self._store = store

    def append(self, entry: Dict[str, Any]) -> None:
        self._store.save_transaction(
            {
                "tx_id": entry.get("tx_id"),
                "trace_id": entry.get("trace_id"),
                "branch_id": entry.get("branch_id"),
                "epoch": entry.get("epoch"),
                "status": entry.get("status"),
                "scopes": entry.get("scopes", []),
                "reason": entry.get("reason"),
            }
        )
        payloads = entry.get("effects_payloads") or []
        for effect_entry in payloads:
            self._store.append_effect(effect_entry)

    def entries(self) -> List[Dict[str, Any]]:
        return self._store.list_effects()


_ALLOWED_TABLES = frozenset(
    {"effects", "frontiers", "artifacts", "transactions", "tool_calls", "idempotency_keys"}
)


class SqliteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS effects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_id TEXT,
                trace_id TEXT,
                branch_id TEXT,
                epoch INTEGER,
                status TEXT,
                scopes TEXT,
                payload TEXT,
                idempotency_key TEXT,
                description TEXT
            )
            """
        )
        self._ensure_column("effects", "description", "description TEXT")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS frontiers (
                trace_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                PRIMARY KEY (trace_id, scope)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                sha256 TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                content_type TEXT,
                size INTEGER NOT NULL,
                data BLOB
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                tx_id TEXT PRIMARY KEY,
                trace_id TEXT,
                branch_id TEXT,
                epoch INTEGER,
                status TEXT,
                scopes TEXT,
                reason TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_calls (
                call_id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                branch_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                tx_id TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                scopes TEXT NOT NULL,
                tool_input TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                key TEXT PRIMARY KEY,
                trace_id TEXT,
                tx_id TEXT,
                status TEXT NOT NULL DEFAULT 'committed',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self._ensure_column("idempotency_keys", "status", "status TEXT NOT NULL DEFAULT 'committed'")
        self._ensure_tool_calls_schema()
        cur.close()
        self._conn.commit()

    def _ensure_tool_calls_schema(self) -> None:
        cur = self._conn.execute("PRAGMA table_info(tool_calls)")
        columns = {row["name"] for row in cur.fetchall()}
        if "call_id" in columns:
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tool_calls_trace_branch
                ON tool_calls(trace_id, branch_id, tool_name, call_id)
                """
            )
            return
        self._conn.execute("ALTER TABLE tool_calls RENAME TO tool_calls_old")
        self._conn.execute(
            """
            CREATE TABLE tool_calls (
                call_id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                branch_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                tx_id TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                scopes TEXT NOT NULL,
                tool_input TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            INSERT INTO tool_calls
            (trace_id, branch_id, tool_name, tx_id, epoch, scopes, tool_input)
            SELECT trace_id, branch_id, tool_name, tx_id, epoch, scopes, tool_input
            FROM tool_calls_old
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tool_calls_trace_branch
            ON tool_calls(trace_id, branch_id, tool_name, call_id)
            """
        )
        self._conn.execute("DROP TABLE tool_calls_old")
        self._conn.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        if table not in _ALLOWED_TABLES:
            raise ValueError(f"Invalid table name: {table}")
        cur = self._conn.execute(f"PRAGMA table_info({table})")
        columns = {row["name"] for row in cur.fetchall()}
        if column not in columns:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    def append_effect(self, entry: Dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO effects
            (tx_id, trace_id, branch_id, epoch, status, scopes, payload, idempotency_key, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.get("tx_id"),
                entry.get("trace_id"),
                entry.get("branch_id"),
                entry.get("epoch"),
                entry.get("status"),
                json.dumps(entry.get("scopes", [])),
                json.dumps(entry.get("payload")),
                entry.get("idempotency_key"),
                entry.get("description"),
            ),
        )
        self._conn.commit()

    def list_effects(self, trace_id: str | None = None) -> List[Dict[str, Any]]:
        if trace_id:
            cur = self._conn.execute(
                "SELECT * FROM effects WHERE trace_id = ? ORDER BY id",
                (trace_id,),
            )
        else:
            cur = self._conn.execute("SELECT * FROM effects ORDER BY id")
        rows = cur.fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "tx_id": row["tx_id"],
                    "trace_id": row["trace_id"],
                    "branch_id": row["branch_id"],
                    "epoch": row["epoch"],
                    "status": row["status"],
                    "scopes": json.loads(row["scopes"] or "[]"),
                    "payload": json.loads(row["payload"] or "null"),
                    "idempotency_key": row["idempotency_key"],
                    "description": row["description"],
                }
            )
        return result

    def advance_frontier(self, trace_id: str, scope: str, epoch: int) -> None:
        current = self.get_frontier(trace_id, scope)
        if epoch <= current:
            return
        self._conn.execute(
            """
            INSERT INTO frontiers (trace_id, scope, epoch)
            VALUES (?, ?, ?)
            ON CONFLICT(trace_id, scope) DO UPDATE SET epoch=excluded.epoch
            """,
            (trace_id, scope, epoch),
        )
        self._conn.commit()

    def get_frontier(self, trace_id: str, scope: str) -> int:
        cur = self._conn.execute(
            "SELECT epoch FROM frontiers WHERE trace_id = ? AND scope = ?",
            (trace_id, scope),
        )
        row = cur.fetchone()
        return int(row["epoch"]) if row else -1

    def save_artifact(self, artifact: ArtifactRef) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO artifacts (sha256, kind, content_type, size, data)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                artifact.sha256,
                artifact.kind,
                artifact.content_type,
                artifact.size,
                artifact.bytes,
            ),
        )
        self._conn.commit()

    def save_transaction(self, entry: Dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO transactions
            (tx_id, trace_id, branch_id, epoch, status, scopes, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.get("tx_id"),
                entry.get("trace_id"),
                entry.get("branch_id"),
                entry.get("epoch"),
                entry.get("status"),
                json.dumps(entry.get("scopes", [])),
                entry.get("reason"),
            ),
        )
        self._conn.commit()

    def get_transaction(self, tx_id: str) -> Optional[Dict[str, Any]]:
        cur = self._conn.execute(
            """
            SELECT tx_id, trace_id, branch_id, epoch, status, scopes, reason
            FROM transactions
            WHERE tx_id = ?
            """,
            (tx_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "tx_id": row["tx_id"],
            "trace_id": row["trace_id"],
            "branch_id": row["branch_id"],
            "epoch": row["epoch"],
            "status": row["status"],
            "scopes": json.loads(row["scopes"] or "[]"),
            "reason": row["reason"],
        }

    def get_artifact(self, sha256: str) -> Optional[ArtifactRef]:
        cur = self._conn.execute(
            "SELECT sha256, kind, content_type, size, data FROM artifacts WHERE sha256 = ?",
            (sha256,),
        )
        row = cur.fetchone()
        if not row:
            return None
        data = row["data"]
        if isinstance(data, memoryview):
            data = data.tobytes()
        return ArtifactRef(
            kind=row["kind"],
            sha256=row["sha256"],
            size=row["size"],
            content_type=row["content_type"],
            bytes=data,
        )

    def save_tool_call(
        self,
        trace_id: str,
        branch_id: str,
        tool_name: str,
        tx_id: str,
        epoch: int,
        scopes: Iterable[str],
        tool_input: Dict[str, Any],
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO tool_calls
            (trace_id, branch_id, tool_name, tx_id, epoch, scopes, tool_input)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                branch_id,
                tool_name,
                tx_id,
                epoch,
                json.dumps(list(scopes)),
                json.dumps(tool_input),
            ),
        )
        self._conn.commit()

    def load_tool_call(
        self, trace_id: str, branch_id: str, tool_name: str
    ) -> Optional[Dict[str, Any]]:
        cur = self._conn.execute(
            """
            SELECT tx_id, epoch, scopes, tool_input
            FROM tool_calls
            WHERE trace_id = ? AND branch_id = ? AND tool_name = ?
            ORDER BY call_id DESC
            LIMIT 1
            """,
            (trace_id, branch_id, tool_name),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "tx_id": row["tx_id"],
            "epoch": int(row["epoch"]),
            "scopes": json.loads(row["scopes"]),
            "tool_input": json.loads(row["tool_input"]),
        }

    def has_idempotency_key(self, key: str) -> bool:
        """Check if idempotency key is committed (pending keys do NOT block retries)."""
        cur = self._conn.execute(
            "SELECT 1 FROM idempotency_keys WHERE key = ? AND status = 'committed'",
            (key,),
        )
        return cur.fetchone() is not None

    def mark_idempotency_key(self, key: str, trace_id: str, tx_id: str) -> None:
        """Record that an idempotency key has been applied (committed)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO idempotency_keys (key, trace_id, tx_id, status) VALUES (?, ?, ?, 'committed')",
            (key, trace_id, tx_id),
        )
        self._conn.commit()

    def mark_idempotency_key_pending(self, key: str, trace_id: str, tx_id: str) -> None:
        """Insert an idempotency key with status='pending' before applying the effect."""
        self._conn.execute(
            "INSERT OR REPLACE INTO idempotency_keys (key, trace_id, tx_id, status) VALUES (?, ?, ?, 'pending')",
            (key, trace_id, tx_id),
        )
        self._conn.commit()

    def mark_idempotency_key_committed(self, key: str) -> None:
        """Transition an idempotency key from 'pending' to 'committed'."""
        self._conn.execute(
            "UPDATE idempotency_keys SET status = 'committed' WHERE key = ?",
            (key,),
        )
        self._conn.commit()

    def get_pending_keys(self) -> List[Tuple[str, str, str]]:
        """Return all keys with status='pending' as (key, trace_id, tx_id) tuples."""
        cur = self._conn.execute(
            "SELECT key, trace_id, tx_id FROM idempotency_keys WHERE status = 'pending'"
        )
        return [(row["key"], row["trace_id"], row["tx_id"]) for row in cur.fetchall()]

    def delete_idempotency_key(self, key: str) -> None:
        """Remove an idempotency key (used during rollback of failed effects)."""
        self._conn.execute(
            "DELETE FROM idempotency_keys WHERE key = ?", (key,)
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
