"""Tests for AppendOnlyLog (A3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from atomix.sinks.append_only_log import AppendOnlyLog
from atomix.sinks.webhook_sink import REDACTED_HEADER_VALUE, _redact_headers


def test_append_and_read(tmp_path: Path):
    log = AppendOnlyLog(tmp_path / "mail.log")
    r1 = log.append({"to": "alice@example.com", "subject": "hi"})
    r2 = log.append({"to": "bob@example.com", "subject": "hi"})
    log.close()
    records = log.read_all()
    assert len(records) == 2
    assert records[0].content_hash == r1.content_hash
    assert records[1].content_hash == r2.content_hash
    # Same payload -> same hash.
    assert (
        AppendOnlyLog(tmp_path / "x.log").append({"a": 1}).content_hash
        == AppendOnlyLog(tmp_path / "y.log").append({"a": 1}).content_hash
    )


def test_open_once_invariant(tmp_path: Path):
    p = tmp_path / "once.log"
    log = AppendOnlyLog(p)
    log.append({"hello": "world"})
    log.close()
    # New instance pointing at the same non-empty file refuses to open.
    log2 = AppendOnlyLog(p)
    with pytest.raises(PermissionError):
        log2.append({"hello": "again"})


def test_no_truncation(tmp_path: Path):
    p = tmp_path / "tail.log"
    log = AppendOnlyLog(p)
    for i in range(100):
        log.append({"i": i})
    log.close()
    # File should have exactly 100 lines.
    text = p.read_text()
    assert sum(1 for line in text.splitlines() if line.strip()) == 100


def test_iteration(tmp_path: Path):
    log = AppendOnlyLog(tmp_path / "iter.log")
    for i in range(5):
        log.append({"i": i})
    log.close()
    items = [rec.payload["i"] for rec in log]
    assert items == [0, 1, 2, 3, 4]


def test_read_all_detects_payload_tampering(tmp_path: Path):
    p = tmp_path / "tamper.log"
    log = AppendOnlyLog(p)
    log.append({"status": "original"})
    log.close()

    text = p.read_text(encoding="utf-8").replace("original", "changed")
    p.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        log.read_all()


def test_webhook_header_redaction():
    headers = {
        "Authorization": "Bearer secret",
        "X-Api-Key": "abc123",
        "X-Trace-Id": "trace-1",
        "Custom-Token": "token-secret",
    }

    redacted = _redact_headers(headers)

    assert redacted["Authorization"] == REDACTED_HEADER_VALUE
    assert redacted["X-Api-Key"] == REDACTED_HEADER_VALUE
    assert redacted["Custom-Token"] == REDACTED_HEADER_VALUE
    assert redacted["X-Trace-Id"] == "trace-1"
