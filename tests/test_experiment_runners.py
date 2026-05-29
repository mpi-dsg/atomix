from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[arg-type]
    return module


run_workload_experiment = _load_script("run_workload_experiment")


def test_webarena_mock_is_rejected_for_full_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "full-webarena.json"
    config.write_text(
        json.dumps(
            {
                "experiment": "E2",
                "run_id": "local-run",
                "workload": "webarena",
                "env": {"WEBARENA_USE_REAL_ENV": "0"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="only allowed for smoke"):
        run_workload_experiment.run(config, tmp_path / "out.json")


def test_osworld_child_success_false_marks_wrapper_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run_command(command, workdir, env, log_prefix):
        child_jsonl = Path(command[command.index("--output-json") + 1])
        child_jsonl.write_text('{"success": false}\n', encoding="utf-8")
        return {"returncode": 0, "duration_s": 0.0}

    monkeypatch.setattr(run_workload_experiment, "_run_command", fake_run_command)
    config = tmp_path / "osworld.json"
    config.write_text(
        json.dumps({"experiment": "E2", "workload": "osworld", "mode": "Tx-Full"}),
        encoding="utf-8",
    )

    assert run_workload_experiment.run(config, tmp_path / "out.json") is False
    payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert payload["summary"]["child_success"] is False


def test_v3_dispatch_routes_local_runners(tmp_path: Path) -> None:
    config = ROOT / "configs" / "experiments" / "e5_b1_semantic.json"
    out = tmp_path / "semantic.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_experiment_dispatch.py"),
            "--config",
            str(config),
            "--output",
            str(out),
        ],
        cwd=ROOT,
        check=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    rows = {row["mode"]: row for row in payload["rows"]}
    assert rows["Tx-Full"]["semantic_invalid_commits"] > 0
    assert rows["Tx-Full+SemanticHook"]["semantic_invalid_commits"] == 0


def test_e7_tx_full_classification_matches_ground_truth(tmp_path: Path) -> None:
    out = tmp_path / "e7.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_e7_compfail.py"),
            "--trials",
            "20",
            "--out",
            str(out),
        ],
        cwd=ROOT,
        check=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    tx_full = [
        cell
        for key, cell in payload["cells"].items()
        if key.startswith("Tx-Full|")
    ]
    assert tx_full
    assert all(cell["classification_mismatches"] == 0 for cell in tx_full)


def test_b2_multiagent_emits_checker_rows(tmp_path: Path) -> None:
    out = tmp_path / "b2.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_b2_multiagent.py"),
            "--schedules",
            "5",
            "--out",
            str(out),
        ],
        cwd=ROOT,
        check=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    rows = json.loads(out.read_text(encoding="utf-8"))["rows"]
    by_baseline = {row["baseline"]: row for row in rows}
    assert by_baseline["Tx-Full"]["violations"] == 0
    assert by_baseline["Tx-NoScopeOnRead"]["violations"] > 0
