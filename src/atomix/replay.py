"""Replay engine for effect logs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from .store import SqliteStore


@dataclass(frozen=True)
class ReplayDiff:
    missing: List[Dict[str, Any]]
    extra: List[Dict[str, Any]]
    mismatched: List[Tuple[Dict[str, Any], Dict[str, Any]]]
    duplicate_keys: List[str] = field(default_factory=list)


class ReplayEngine:
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def committed_effects(self, trace_id: str) -> List[Dict[str, Any]]:
        entries = self._store.list_effects(trace_id=trace_id)
        return [entry for entry in entries if entry.get("status") == "committed"]

    def compare_effects(
        self, expected: List[Dict[str, Any]], actual: List[Dict[str, Any]]
    ) -> ReplayDiff:
        def key_for(entry: Dict[str, Any]) -> str:
            return entry.get("idempotency_key") or entry.get("description") or entry.get("tx_id")

        # Detect duplicates
        duplicate_keys: List[str] = []
        expected_map: Dict[str, Dict[str, Any]] = {}
        for entry in expected:
            k = key_for(entry)
            if k in expected_map:
                duplicate_keys.append(k)
            expected_map[k] = entry

        actual_map: Dict[str, Dict[str, Any]] = {}
        for entry in actual:
            k = key_for(entry)
            if k in actual_map:
                duplicate_keys.append(k)
            actual_map[k] = entry

        missing_keys = expected_map.keys() - actual_map.keys()
        extra_keys = actual_map.keys() - expected_map.keys()

        missing = [expected_map[key] for key in missing_keys]
        extra = [actual_map[key] for key in extra_keys]

        mismatched: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for key in expected_map.keys() & actual_map.keys():
            if expected_map[key] != actual_map[key]:
                mismatched.append((expected_map[key], actual_map[key]))

        return ReplayDiff(missing=missing, extra=extra, mismatched=mismatched, duplicate_keys=duplicate_keys)

    def compare_committed(
        self, trace_id: str, expected: List[Dict[str, Any]]
    ) -> ReplayDiff:
        actual = self.committed_effects(trace_id)
        return self.compare_effects(expected, actual)
