"""Append-only log: open once for write, never re-open, never truncate.

Used as the substrate for the SMTP/webhook sinks in E3 mailbox class and
E4 irreversible-effect runs. The "open once" property lets the harness
verify after-the-fact that no replay tampered with the record.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class LogRecord:
    timestamp: str  # ISO-8601 UTC
    content_hash: str  # sha256 of payload bytes
    payload: dict


class AppendOnlyLog:
    """Append-only file. Opens once on first write; never re-opens.

    - One process writes; multiple processes read.
    - Any attempt to re-open for write after the first raises PermissionError.
    - Records are JSON lines: {"ts": ..., "hash": ..., "payload": ...}
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = None
        self._lock = threading.Lock()
        self._opened = False

    def _ensure_open(self) -> None:
        if self._opened:
            return
        # Refuse to re-open if a previous process already wrote to this log.
        if self._path.exists() and self._path.stat().st_size > 0:
            raise PermissionError(
                f"AppendOnlyLog at {self._path} already has data; "
                "this instance refuses to re-open a non-empty log."
            )
        # O_APPEND ensures every write goes to end; O_CREAT creates if absent.
        # We do not pass O_TRUNC: the log must never be truncated.
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        fd = os.open(self._path, flags, 0o644)
        self._fh = os.fdopen(fd, "a", encoding="utf-8")
        self._opened = True

    def append(self, payload: dict) -> LogRecord:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        h = hashlib.sha256(body).hexdigest()
        ts = datetime.now(timezone.utc).isoformat()
        record = LogRecord(timestamp=ts, content_hash=h, payload=payload)
        line = json.dumps({"ts": ts, "hash": h, "payload": payload}) + "\n"
        with self._lock:
            self._ensure_open()
            assert self._fh is not None
            self._fh.write(line)
            self._fh.flush()
            os.fsync(self._fh.fileno())
        return record

    def read_all(self) -> list[LogRecord]:
        if not self._path.exists():
            return []
        out: list[LogRecord] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                body = json.dumps(d["payload"], sort_keys=True).encode("utf-8")
                expected_hash = hashlib.sha256(body).hexdigest()
                if d["hash"] != expected_hash:
                    raise ValueError(
                        f"AppendOnlyLog record hash mismatch in {self._path}"
                    )
                out.append(
                    LogRecord(
                        timestamp=d["ts"],
                        content_hash=d["hash"],
                        payload=d["payload"],
                    )
                )
        return out

    def __iter__(self) -> Iterator[LogRecord]:
        return iter(self.read_all())

    def close(self) -> None:
        with self._lock:
            if self._fh:
                self._fh.flush()
                os.fsync(self._fh.fileno())
                self._fh.close()
                self._fh = None
