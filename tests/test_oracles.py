"""Tests for A2 oracles."""

from __future__ import annotations

from pathlib import Path

from atomix.oracles import clean_success
from atomix.oracles.osworld import (
    FsSnapshot,
    OSWorldEvaluationContext,
    diff_fs,
    diff_processes,
    take_fs_snapshot,
)
from atomix.oracles.taubench import TauBenchEvaluationContext, diff_db
from atomix.oracles.webarena import WebArenaEvaluationContext


def test_osworld_clean_success_with_no_residue(tmp_path: Path):
    snap = FsSnapshot()
    ctx = OSWorldEvaluationContext(
        evaluation_fn=lambda: True, fs_before=snap, fs_after=snap,
        proc_before=set(), proc_after=set(),
    )
    clean, residue = clean_success("osworld", task_id="t1", ctx=ctx)
    assert clean is True
    assert residue == []


def test_osworld_residue_detected(tmp_path: Path):
    (tmp_path / "before").mkdir()
    before = take_fs_snapshot([str(tmp_path / "before")])
    (tmp_path / "before" / "file.txt").write_text("hello")
    after = take_fs_snapshot([str(tmp_path / "before")])
    diffs = diff_fs(before, after)
    assert len(diffs) == 1
    assert diffs[0].note == "create"


def test_osworld_goal_failed_with_no_residue_is_not_clean(tmp_path: Path):
    snap = FsSnapshot()
    ctx = OSWorldEvaluationContext(
        evaluation_fn=lambda: False, fs_before=snap, fs_after=snap,
        proc_before=set(), proc_after=set(),
    )
    clean, residue = clean_success("osworld", task_id="t1", ctx=ctx)
    assert clean is False
    assert residue == []


def test_taubench_db_diff():
    before = {"orders": {"7": {"id": 7, "total": 100}}}
    after = {
        "orders": {
            "7": {"id": 7, "total": 100},
            "8": {"id": 8, "total": 200},
        }
    }
    diffs = diff_db(before, after)
    assert len(diffs) == 1
    assert diffs[0].note == "insert"
    assert diffs[0].key == "orders:8"


def test_taubench_clean_success():
    snap = {"orders": {}, "customers": {}, "refunds": {}}
    ctx = TauBenchEvaluationContext(
        evaluation_fn=lambda: True, db_before=snap, db_after=snap,
    )
    clean, residue = clean_success("taubench", task_id="t1", ctx=ctx)
    assert clean is True
    assert residue == []


def test_webarena_dom_diff():
    ctx = WebArenaEvaluationContext(
        evaluation_fn=lambda: True,
        dom_before={"http://shop": {"#cart": "<div>0</div>"}},
        dom_after={"http://shop": {"#cart": "<div>1</div>"}},
    )
    clean, residue = clean_success("webarena", task_id="t1", ctx=ctx)
    assert clean is False
    assert any(r.note == "dom_modify" for r in residue)


def test_diff_processes_detects_new_command():
    before = {"123:bash"}
    after = {"123:bash", "456:firefox"}
    diffs = diff_processes(before, after)
    assert len(diffs) == 1
    assert diffs[0].after == "firefox"
