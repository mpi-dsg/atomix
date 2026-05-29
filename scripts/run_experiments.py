#!/usr/bin/env python3
"""Run Atomix workloads across multiple modes and emit metrics.

Supports modes: Tx-Full (default Atomix), No-Frontier (frontier gating disabled),
No-Tx (direct application), Saga (compensations without frontier), and produces
JSON results compatible with `compute_metrics.py` and `plot_results.py`.
Config format (JSON):
{
  "trace_id": "sample-trace",
  "tasks": [
    {"tool": "file_write", "args": {"path": "results/sample.txt", "content": "hello"}}
  ]
}
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

import subprocess

from atomix.adapters.filesystem import FileWriteAdapter
from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.integrations.workloads.osworld.adapters import (
    AppendFileAdapter,
    ReadFileAdapter,
    WriteFileAdapter,
    append_file,
    plan_append_file,
    plan_write_file,
    read_file,
    write_file,
)
from atomix.integrations.workloads.webarena.adapters import WebArenaActionAdapter
from atomix.injector import FaultProfile
from atomix.runtime import AtomixRuntime
from atomix.tool_result import ToolMeta, ToolResult

MODES = {"Tx-Full", "No-Frontier", "No-Tx", "Saga"}
SUPPORTED_WORKLOADS = {
    "sample",
    "webarena",
    "osworld",
    "theagentcompany",
    "taubench",
}


def _apply_effect(effect: Effect) -> None:
    if effect.description.startswith("read:"):
        return
    payload = effect.payload or {}
    if "path" in payload and "content" in payload:
        path = Path(payload["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(payload.get("content", "")), encoding="utf-8")
    elif "path" in payload and "appended" in payload:
        path = Path(payload["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(str(payload.get("appended", "")))


def _load_config(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "tasks" not in data:
        raise ValueError("Config must be an object with a 'tasks' array")
    return data


def _iter_steps(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    for item in config.get("tasks", []):
        if "steps" in item:
            for step in item.get("steps", []):
                if isinstance(step, dict):
                    steps.append(step)
            continue
        if isinstance(item, dict):
            steps.append(item)
    return steps


def _register_adapters(runtime: AtomixRuntime) -> None:
    runtime.register_adapter("file_write", FileWriteAdapter())
    runtime.register_adapter("write_file", WriteFileAdapter())
    runtime.register_adapter("append_file", AppendFileAdapter())
    runtime.register_adapter("read_file", ReadFileAdapter())
    runtime.register_adapter("webarena_action", WebArenaActionAdapter())


def _plan_tool(tool: str, args: Dict[str, Any]) -> Any:
    if tool == "file_write":
        return args
    if tool == "write_file":
        return plan_write_file(**args)
    if tool == "append_file":
        return plan_append_file(**args)
    if tool == "read_file":
        return read_file(**args)
    if tool == "webarena_action":
        return args
    raise ValueError(f"unsupported tool {tool}")


def _no_tx_apply(task: Dict[str, Any]) -> Dict[str, Any]:
    start = time.perf_counter()
    tool = task.get("tool")
    args = task.get("args", {})
    try:
        if tool in {"file_write", "write_file"}:
            write_file(path=args["path"], content=str(args.get("content", "")))
            effects_applied = 1
        elif tool == "append_file":
            append_file(path=args["path"], content=str(args.get("content", "")))
            effects_applied = 1
        elif tool == "read_file":
            read_file(path=args["path"])
            effects_applied = 0
        elif tool == "webarena_action":
            effects_applied = 1
        else:
            raise ValueError(f"unsupported tool {tool}")
        duration_ms = (time.perf_counter() - start) * 1000
        return {
            "mode": "No-Tx",
            "success": True,
            "partial_state": False,
            "duration_ms": duration_ms,
            "effects_applied": effects_applied,
        }
    except Exception as exc:  # noqa: BLE001
        duration_ms = (time.perf_counter() - start) * 1000
        return {
            "mode": "No-Tx",
            "success": False,
            "partial_state": True,
            "duration_ms": duration_ms,
            "error": str(exc),
        }


def _run_with_runtime(
    mode: str, runtime: AtomixRuntime, config: Dict[str, Any]
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    trace_id = str(config.get("trace_id", "trace"))
    for task in _iter_steps(config):
        tool = task.get("tool")
        args = task.get("args", {})
        epoch = runtime.epochs.next(trace_id=trace_id)
        start = time.perf_counter()
        try:
            if mode == "No-Frontier":
                runtime.advance_frontier(
                    set(runtime.adapters.get(tool).scopes(args)), Epoch(10**9, trace_id)
                )
            _, tx = runtime.run_tool(tool, lambda **kwargs: _plan_tool(tool, kwargs), args, epoch)
            duration_ms = (time.perf_counter() - start) * 1000
            results.append(
                {
                    "mode": mode,
                    "success": True,
                    "partial_state": False,
                    "duration_ms": duration_ms,
                    "effects_applied": len(tx.effects),
                }
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.perf_counter() - start) * 1000
            results.append(
                {
                    "mode": mode,
                    "success": False,
                    "partial_state": True,
                    "duration_ms": duration_ms,
                    "error": str(exc),
                }
            )
    return results


def _run_saga(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    trace_id = str(config.get("trace_id", "trace"))
    adapters = {
        "file_write": FileWriteAdapter(),
        "write_file": WriteFileAdapter(),
        "append_file": AppendFileAdapter(),
        "read_file": ReadFileAdapter(),
        "webarena_action": WebArenaActionAdapter(),
    }
    for task in _iter_steps(config):
        tool = task.get("tool")
        args = task.get("args", {})
        epoch = Epoch(0, trace_id=trace_id)
        start = time.perf_counter()
        effect: Effect | None = None
        try:
            adapter = adapters.get(tool)
            if adapter is None:
                raise ValueError(f"unsupported tool {tool}")
            meta = ToolMeta(
                tool_name=tool, trace_id=trace_id, branch_id=None, attempt=0
            )
            tool_result = ToolResult(
                success=True, output=_plan_tool(tool, args), error=None, meta=meta
            )
            effect = adapter.to_effect(args, tool_result, epoch)
            _apply_effect(effect)
            duration_ms = (time.perf_counter() - start) * 1000
            results.append(
                {
                    "mode": "Saga",
                    "success": True,
                    "partial_state": False,
                    "duration_ms": duration_ms,
                    "effects_applied": 1,
                }
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.perf_counter() - start) * 1000
            try:
                if effect and effect.compensation:
                    effect.compensation()
            except Exception:
                pass
            results.append(
                {
                    "mode": "Saga",
                    "success": False,
                    "partial_state": True,
                    "duration_ms": duration_ms,
                    "error": str(exc),
                }
            )
    return results


def _assert_dataset(workload: str | None) -> None:
    if workload is None:
        return
    if workload not in SUPPORTED_WORKLOADS:
        raise RuntimeError(
            f"Unsupported workload '{workload}'. Supported: {sorted(SUPPORTED_WORKLOADS)}"
        )
    data_root = os.environ.get("DATA_ROOT", "./data")
    if workload == "webarena":
        root = Path(os.environ.get("WEBARENA_DATA_DIR", f"{data_root}/webarena"))
        if not root.exists():
            raise RuntimeError(
                f"WebArena dataset missing at {root}. Run scripts/download_data.sh (set WEBARENA_DATA_DIR or DATA_ROOT)."
            )
    if workload == "theagentcompany":
        root = Path(
            os.environ.get("THEAGENTCOMPANY_DIR", f"{data_root}/theagentcompany")
        )
        marker = root / ".tac_setup_done"
        if not marker.exists():
            raise RuntimeError(
                "TheAgentCompany not set up. Run SETUP_THEAGENTCOMPANY=1 scripts/download_data.sh."
            )
    if workload == "taubench":
        root = Path(os.environ.get("TAUBENCH_DIR", f"{data_root}/tau2-bench"))
        marker = root / ".tau2_setup_done"
        if not marker.exists():
            raise RuntimeError(
                "tau2-bench not set up. Run SETUP_TAUBENCH=1 scripts/download_data.sh."
            )


def _ensure_workload_ready(workload: str | None, auto_setup: bool) -> None:
    if workload is None:
        return
    if workload not in SUPPORTED_WORKLOADS:
        raise RuntimeError(
            f"Unsupported workload '{workload}'. Supported: {sorted(SUPPORTED_WORKLOADS)}"
        )
    if not auto_setup:
        _assert_dataset(workload)
        return
    if workload == "theagentcompany":
        script = Path("scripts/setup_theagentcompany.sh")
        if script.exists():
            subprocess.run(["bash", str(script)], check=True)
        _assert_dataset(workload)
        return
    if workload == "taubench":
        script = Path("scripts/setup_taubench.sh")
        if script.exists():
            subprocess.run(["bash", str(script)], check=True)
        _assert_dataset(workload)
        return
    _assert_dataset(workload)


def run(
    modes: list[str],
    config_path: Path,
    output: Path,
    fault_profile: dict[str, float] | None = None,
    workload: str | None = None,
    auto_setup: bool = False,
) -> Path:
    invalid = [m for m in modes if m not in MODES]
    if invalid:
        raise ValueError(f"modes must be subset of {sorted(MODES)}, got {invalid}")

    _ensure_workload_ready(workload, auto_setup=auto_setup)

    cfg = _load_config(config_path)
    all_results: List[Dict[str, Any]] = []

    for mode in modes:
        if mode == "No-Tx":
            all_results.extend(_no_tx_apply(task) for task in _iter_steps(cfg))
            continue
        if mode == "Saga":
            all_results.extend(_run_saga(cfg))
            continue
        runtime = AtomixRuntime(
            apply_effect=_apply_effect,
            fault_profile=FaultProfile(**fault_profile) if fault_profile else None,
        )
        _register_adapters(runtime)
        all_results.extend(_run_with_runtime(mode, runtime, cfg))

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--modes",
        default="Tx-Full",
        help=f"Comma-separated subset of {sorted(MODES)}",
    )
    parser.add_argument(
        "--config",
        default="configs/sample_workload.json",
        help="Path to workload config",
    )
    parser.add_argument(
        "--output", default="results/metrics.json", help="Where to write results JSON"
    )
    parser.add_argument(
        "--fault-profile",
        help="JSON for fault profile (duplicate_probability, exception_probability, min_delay_s, max_delay_s)",
    )
    parser.add_argument(
        "--workload",
        help=f"Workload name ({', '.join(sorted(SUPPORTED_WORKLOADS))})",
    )
    parser.add_argument(
        "--auto-setup",
        action="store_true",
        help="Attempt workload setup when missing (theagentcompany, taubench)",
    )
    args = parser.parse_args()

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    fault_profile = json.loads(args.fault_profile) if args.fault_profile else None

    out = run(
        modes,
        Path(args.config),
        Path(args.output),
        fault_profile=fault_profile,
        workload=args.workload,
        auto_setup=args.auto_setup,
    )
    print(f"Wrote results to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
