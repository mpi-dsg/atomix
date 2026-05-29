"""Comprehensive tests for EffectLog."""

from __future__ import annotations

import json
from pathlib import Path


from atomix.transactions import EffectLog


class TestEffectLogInMemory:
    """Tests for in-memory EffectLog behavior."""

    def test_append_and_retrieve(self) -> None:
        """Basic append and retrieve operations."""
        log = EffectLog()
        log.append({"key": "value1"})
        log.append({"key": "value2"})
        entries = log.entries()
        assert len(entries) == 2
        assert entries[0] == {"key": "value1"}
        assert entries[1] == {"key": "value2"}

    def test_entries_returns_copy(self) -> None:
        """entries() should return a copy, not the internal list."""
        log = EffectLog()
        log.append({"a": 1})
        entries = log.entries()
        entries.append({"b": 2})  # Modify returned list
        assert len(log.entries()) == 1  # Internal list unchanged

    def test_empty_log(self) -> None:
        """Empty log should return empty list."""
        log = EffectLog()
        assert log.entries() == []

    def test_complex_entries(self) -> None:
        """Log should handle complex nested structures."""
        log = EffectLog()
        entry = {
            "tx_id": "abc-123",
            "effects": ["effect1", "effect2"],
            "nested": {"a": {"b": {"c": 1}}},
            "list_of_dicts": [{"x": 1}, {"y": 2}],
        }
        log.append(entry)
        assert log.entries()[0] == entry


class TestEffectLogPersistence:
    """Tests for file-backed EffectLog."""

    def test_writes_to_file(self, tmp_path: Path) -> None:
        """Entries should be written as JSONL to file."""
        log_path = tmp_path / "effects.jsonl"
        log = EffectLog(path=log_path)
        log.append({"tx_id": "tx1", "status": "committed"})
        log.append({"tx_id": "tx2", "status": "aborted"})

        # Read file directly
        content = log_path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"tx_id": "tx1", "status": "committed"}
        assert json.loads(lines[1]) == {"tx_id": "tx2", "status": "aborted"}

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Should create parent directories if they don't exist."""
        log_path = tmp_path / "nested" / "dirs" / "effects.jsonl"
        log = EffectLog(path=log_path)
        log.append({"test": True})
        assert log_path.exists()
        assert log_path.parent.exists()

    def test_appends_to_existing_file(self, tmp_path: Path) -> None:
        """Should append to file, not overwrite."""
        log_path = tmp_path / "effects.jsonl"
        # First log instance
        log1 = EffectLog(path=log_path)
        log1.append({"seq": 1})
        # Second log instance (simulating restart)
        log2 = EffectLog(path=log_path)
        log2.append({"seq": 2})

        content = log_path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        assert len(lines) == 2

    def test_unicode_content(self, tmp_path: Path) -> None:
        """Should handle unicode content correctly."""
        log_path = tmp_path / "effects.jsonl"
        log = EffectLog(path=log_path)
        test_message = "Hello 世界 🌍"
        log.append({"message": test_message})

        content = log_path.read_text(encoding="utf-8")
        entry = json.loads(content.strip())
        assert entry["message"] == test_message

    def test_special_json_characters(self, tmp_path: Path) -> None:
        """Should escape special JSON characters correctly."""
        log_path = tmp_path / "effects.jsonl"
        log = EffectLog(path=log_path)
        log.append({"path": 'C:\\Users\\test\\file.txt', "quote": 'He said "hello"'})

        content = log_path.read_text(encoding="utf-8")
        entry = json.loads(content.strip())
        assert entry["path"] == 'C:\\Users\\test\\file.txt'
        assert entry["quote"] == 'He said "hello"'

    def test_in_memory_and_file_stay_in_sync(self, tmp_path: Path) -> None:
        """In-memory entries and file should contain same data."""
        log_path = tmp_path / "effects.jsonl"
        log = EffectLog(path=log_path)

        for i in range(5):
            log.append({"seq": i})

        # Compare in-memory
        mem_entries = log.entries()

        # Compare file
        file_entries = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").strip().split("\n")
        ]

        assert mem_entries == file_entries
