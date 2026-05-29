"""Tests for A5 serializability checker."""

from __future__ import annotations

import json
from pathlib import Path

from atomix.checker.conflict_graph import OpRecord, build_graph
from atomix.checker.serializability import check_log, clopper_pearson_upper


def _ops(*records):
    return [OpRecord(*r) for r in records]


def test_serializable_schedule_no_cycles():
    # tx1 reads x, writes y; tx2 reads y, writes z. Linear, no cycle.
    ops = _ops(
        ("tx1", "read", "x", "h1", "2026-01-01T00:00:00"),
        ("tx1", "write", "y", "h2", "2026-01-01T00:00:01"),
        ("tx1", "commit", "", "", "2026-01-01T00:00:02"),
        ("tx2", "read", "y", "h2", "2026-01-01T00:00:03"),
        ("tx2", "write", "z", "h3", "2026-01-01T00:00:04"),
        ("tx2", "commit", "", "", "2026-01-01T00:00:05"),
    )
    g = build_graph(ops, substrate="filesystem")
    # tx1 -> tx2 (read-from on y) is acyclic.
    assert "tx2" in g.edges.get("tx1", set())
    # No reverse edge.
    assert "tx1" not in g.edges.get("tx2", set())


def test_known_cycle_detected(tmp_path: Path):
    # Classic dirty-write cycle:
    # tx1 writes x; tx2 reads x and writes y; tx1 reads y. → cycle tx1 -> tx2 -> tx1.
    log = [
        {"tx_id": "tx1", "op_kind": "write", "scope": "x", "value_hash": "h1", "ts": "2026-01-01T00:00:00", "trace_id": "r1"},
        {"tx_id": "tx2", "op_kind": "read", "scope": "x", "value_hash": "h1", "ts": "2026-01-01T00:00:01", "trace_id": "r1"},
        {"tx_id": "tx2", "op_kind": "write", "scope": "y", "value_hash": "h2", "ts": "2026-01-01T00:00:02", "trace_id": "r1"},
        {"tx_id": "tx1", "op_kind": "read", "scope": "y", "value_hash": "h2", "ts": "2026-01-01T00:00:03", "trace_id": "r1"},
        {"tx_id": "tx1", "op_kind": "commit", "scope": "", "value_hash": "", "ts": "2026-01-01T00:00:04", "trace_id": "r1"},
        {"tx_id": "tx2", "op_kind": "commit", "scope": "", "value_hash": "", "ts": "2026-01-01T00:00:05", "trace_id": "r1"},
    ]
    p = tmp_path / "log.jsonl"
    with p.open("w") as f:
        for rec in log:
            f.write(json.dumps(rec) + "\n")
    result = check_log(p, substrate="filesystem")
    assert result.violations_found == 1
    assert result.cycles[0].cycle == ["tx1", "tx2"] or result.cycles[0].cycle == ["tx2", "tx1"]


def test_alias_induced_cycle_canonical_vs_naive(tmp_path: Path):
    """An alias-induced cycle: two scopes that name the same fs entry via
    different paths. Canonical scopes find the cycle; naive string scopes
    miss it.
    """
    log = [
        {"tx_id": "tx1", "op_kind": "write", "scope": "fs:/repo/src/main.py", "value_hash": "h1", "ts": "2026-01-01T00:00:00", "trace_id": "r1"},
        {"tx_id": "tx2", "op_kind": "read", "scope": "fs:/repo/src/../src/main.py", "value_hash": "h1", "ts": "2026-01-01T00:00:01", "trace_id": "r1"},
        {"tx_id": "tx2", "op_kind": "write", "scope": "fs:/repo/y", "value_hash": "h2", "ts": "2026-01-01T00:00:02", "trace_id": "r1"},
        {"tx_id": "tx1", "op_kind": "read", "scope": "fs:/repo/y", "value_hash": "h2", "ts": "2026-01-01T00:00:03", "trace_id": "r1"},
        {"tx_id": "tx1", "op_kind": "commit", "scope": "", "value_hash": "", "ts": "2026-01-01T00:00:04", "trace_id": "r1"},
        {"tx_id": "tx2", "op_kind": "commit", "scope": "", "value_hash": "", "ts": "2026-01-01T00:00:05", "trace_id": "r1"},
    ]
    p = tmp_path / "alias.jsonl"
    with p.open("w") as f:
        for rec in log:
            f.write(json.dumps(rec) + "\n")
    canon = check_log(p, substrate="filesystem", canonicalize_scopes=True)
    naive = check_log(p, substrate="filesystem", canonicalize_scopes=False)
    assert canon.violations_found == 1
    assert naive.violations_found == 0


def test_clopper_pearson_bounds():
    # 0/100 -> low upper bound; ~0.036 by exact formula.
    upper = clopper_pearson_upper(0, 100)
    assert 0.02 < upper < 0.05
    # 1/100 -> ~0.054
    upper = clopper_pearson_upper(1, 100)
    assert 0.04 < upper < 0.07
    # All successes
    upper = clopper_pearson_upper(100, 100)
    assert upper == 1.0
    # Empty
    assert clopper_pearson_upper(0, 0) == 1.0


def test_independent_traces_do_not_form_cross_trace_cycle(tmp_path: Path):
    log = [
        {"tx_id": "tx1", "op_kind": "write", "scope": "x", "value_hash": "h1", "ts": "2026-01-01T00:00:00", "trace_id": "r1"},
        {"tx_id": "tx2", "op_kind": "read", "scope": "x", "value_hash": "h1", "ts": "2026-01-01T00:00:01", "trace_id": "r1"},
        {"tx_id": "tx2", "op_kind": "write", "scope": "y", "value_hash": "h2", "ts": "2026-01-01T00:00:02", "trace_id": "r2"},
        {"tx_id": "tx1", "op_kind": "read", "scope": "y", "value_hash": "h2", "ts": "2026-01-01T00:00:03", "trace_id": "r2"},
    ]
    p = tmp_path / "cross_trace.jsonl"
    with p.open("w") as f:
        for rec in log:
            f.write(json.dumps(rec) + "\n")

    result = check_log(p, substrate="filesystem")

    assert result.schedules_checked == 2
    assert result.violations_found == 0
    assert result.cycles == []


def test_multiple_cycles_count_as_one_violating_schedule(tmp_path: Path):
    log = [
        {"tx_id": "a1", "op_kind": "write", "scope": "x", "value_hash": "h1", "ts": "2026-01-01T00:00:00", "trace_id": "r1"},
        {"tx_id": "a2", "op_kind": "read", "scope": "x", "value_hash": "h1", "ts": "2026-01-01T00:00:01", "trace_id": "r1"},
        {"tx_id": "a2", "op_kind": "write", "scope": "y", "value_hash": "h2", "ts": "2026-01-01T00:00:02", "trace_id": "r1"},
        {"tx_id": "a1", "op_kind": "read", "scope": "y", "value_hash": "h2", "ts": "2026-01-01T00:00:03", "trace_id": "r1"},
        {"tx_id": "b1", "op_kind": "write", "scope": "m", "value_hash": "h3", "ts": "2026-01-01T00:00:04", "trace_id": "r1"},
        {"tx_id": "b2", "op_kind": "read", "scope": "m", "value_hash": "h3", "ts": "2026-01-01T00:00:05", "trace_id": "r1"},
        {"tx_id": "b2", "op_kind": "write", "scope": "n", "value_hash": "h4", "ts": "2026-01-01T00:00:06", "trace_id": "r1"},
        {"tx_id": "b1", "op_kind": "read", "scope": "n", "value_hash": "h4", "ts": "2026-01-01T00:00:07", "trace_id": "r1"},
    ]
    p = tmp_path / "multi_cycle.jsonl"
    with p.open("w") as f:
        for rec in log:
            f.write(json.dumps(rec) + "\n")

    result = check_log(p, substrate="filesystem")

    assert result.schedules_checked == 1
    assert result.violations_found == 1
    assert len(result.cycles) == 2
