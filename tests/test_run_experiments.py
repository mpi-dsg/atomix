import json
from pathlib import Path

import importlib.util
import sys

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_experiments.py"
spec = importlib.util.spec_from_file_location("run_experiments", SCRIPT_PATH)
assert spec and spec.loader
run_experiments = importlib.util.module_from_spec(spec)
sys.modules["run_experiments"] = run_experiments
spec.loader.exec_module(run_experiments)  # type: ignore[arg-type]


def test_run_experiments_tx_full(tmp_path: Path):
    config = tmp_path / "config.json"
    output = tmp_path / "metrics.json"
    files_dir = tmp_path / "out"
    config.write_text(
        json.dumps(
            {
                "trace_id": "t1",
                "tasks": [
                    {
                        "tool": "file_write",
                        "args": {"path": str(files_dir / "a.txt"), "content": "hi"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    run_experiments.run(["Tx-Full"], config, output)

    metrics = json.loads(output.read_text(encoding="utf-8"))
    assert metrics[0]["success"] is True
    assert (files_dir / "a.txt").read_text(encoding="utf-8") == "hi"


def test_run_experiments_no_tx(tmp_path: Path):
    config = tmp_path / "config.json"
    output = tmp_path / "metrics.json"
    files_dir = tmp_path / "out"
    config.write_text(
        json.dumps(
            {
                "trace_id": "t2",
                "tasks": [
                    {
                        "tool": "file_write",
                        "args": {"path": str(files_dir / "b.txt"), "content": "yo"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    run_experiments.run(["No-Tx"], config, output)

    metrics = json.loads(output.read_text(encoding="utf-8"))
    assert metrics[0]["success"] is True
    assert (files_dir / "b.txt").read_text(encoding="utf-8") == "yo"


def test_run_experiments_no_frontier_and_saga(tmp_path: Path):
    config = tmp_path / "config.json"
    output = tmp_path / "metrics.json"
    files_dir = tmp_path / "out"
    config.write_text(
        json.dumps(
            {
                "trace_id": "t3",
                "tasks": [
                    {
                        "tool": "file_write",
                        "args": {"path": str(files_dir / "c.txt"), "content": "nf"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    run_experiments.run(["No-Frontier", "Saga"], config, output)

    metrics = json.loads(output.read_text(encoding="utf-8"))
    modes = {entry["mode"] for entry in metrics}
    assert {"No-Frontier", "Saga"} == modes
    assert (files_dir / "c.txt").read_text(encoding="utf-8") == "nf"


def test_run_experiments_nested_workload_steps(tmp_path: Path):
    config = tmp_path / "config.json"
    output = tmp_path / "metrics.json"
    target = tmp_path / "out.txt"
    config.write_text(
        json.dumps(
            {
                "trace_id": "nested",
                "tasks": [
                    {
                        "id": "task-1",
                        "steps": [
                            {
                                "tool": "write_file",
                                "args": {"path": str(target), "content": "hello"},
                            },
                            {
                                "tool": "append_file",
                                "args": {"path": str(target), "content": " world"},
                            },
                            {"tool": "read_file", "args": {"path": str(target)}},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    run_experiments.run(["Tx-Full", "No-Tx", "Saga"], config, output)

    metrics = json.loads(output.read_text(encoding="utf-8"))
    assert len(metrics) == 9
    assert all(entry["success"] for entry in metrics)
    assert target.read_text(encoding="utf-8") == "hello world"
