#!/usr/bin/env python3
"""Dispatch experiment configs to appropriate runners."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _run_subprocess(script: Path, args: list[str]) -> None:
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    subprocess.run([sys.executable, str(script)] + args, check=True, env=env)


def _run_out_script(script: Path, output: Path, args: list[str] | None = None) -> None:
    _run_subprocess(script, [*(args or []), "--out", str(output)])


def run(
    config_path: Path, output: Path, manifest: Path | None, mode: str | None
) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if mode:
        config["mode"] = mode
    experiment = str(config.get("experiment", "")).upper()
    workload = str(config.get("workload", ""))

    script_dir = Path(__file__).resolve().parent

    if experiment in {"E1", "B1"} and workload in {"clean_success", "b1_clean_success"}:
        _run_out_script(script_dir / "run_b1_clean_success.py", output)
        return

    if experiment in {"E2", "B2"} and workload in {"multiagent_taubench", "b2_multiagent"}:
        _run_out_script(script_dir / "run_b2_multiagent.py", output)
        return

    if experiment in {"E2-ABLATIONS", "E2_ABLATIONS"} or workload == "b2_ablations":
        _run_out_script(script_dir / "run_b2_composition_ablations.py", output)
        return

    if experiment in {"E4", "B3"} and workload in {"irreversible_sink", "b3_irreversible"}:
        _run_out_script(script_dir / "run_b3_irreversible.py", output)
        return

    if experiment in {"E4-FP-SWEEP", "E4_FP_SWEEP"} or workload == "b3_fp_sweep":
        _run_out_script(script_dir / "run_b3_fp_sweep_real_sink.py", output)
        return

    if experiment in {"E5-B1", "E5_B1"} or workload == "semantic_boundary":
        _run_out_script(script_dir / "run_e5_b1_semantic.py", output)
        return

    if experiment in {"B5", "PORTS"} or workload == "ports":
        _run_out_script(script_dir / "run_b5_ports.py", output)
        return

    if experiment in {"B6", "SPEC-WEBARENA", "SPEC_WEBARENA"} or workload == "spec_webarena":
        _run_out_script(script_dir / "run_b6_spec_webarena_verify.py", output)
        return

    if experiment in {"B9", "AGENT-FAULTS", "AGENT_FAULTS"} or workload == "agent_faults":
        _run_subprocess(script_dir / "run_agent_induced_faults.py", ["--output", str(output)])
        return

    if experiment in {"E5-B2", "E5_B2"} or workload == "frontier_contract":
        _run_out_script(script_dir / "run_e5_b2_frontier_contract.py", output)
        return

    if experiment in {"E5-B3", "E5_B3"} or workload == "crash_window":
        _run_out_script(script_dir / "run_e5_b3_crash_window.py", output)
        return

    if experiment == "E6" or workload == "aliasing":
        _run_out_script(script_dir / "run_e6_aliasing.py", output)
        return

    if experiment == "E7" or workload == "compfail":
        _run_out_script(script_dir / "run_e7_compfail.py", output)
        return

    if experiment == "E8" or workload == "granularity":
        _run_out_script(script_dir / "run_e8_granularity.py", output)
        return

    if experiment == "E9" or workload == "correlated_sensitivity":
        _run_out_script(script_dir / "run_b8_correlated_sensitivity.py", output)
        return

    if experiment == "E10" or workload == "annotation_errors":
        _run_out_script(script_dir / "run_e10_annotation_errors.py", output)
        return

    if workload in {
        "microbench",
        "synthetic_parallel",
        "contention_sweep",
        "out_of_order",
    } or experiment in {
        "E3",
        "E4",
        "E3.4",
        "E3.2",
    }:
        _run_subprocess(
            script_dir / "run_microbenchmarks.py",
            ["--config", str(config_path), "--output", str(output)],
        )
        return

    if workload in {"speculative_discard", "speculation"} or experiment in {
        "E3.3",
    }:
        args = ["--config", str(config_path), "--output", str(output)]
        if mode:
            args.extend(["--mode", mode])
        _run_subprocess(script_dir / "run_speculation_sim.py", args)
        return

    if workload == "fault_recovery" or experiment == "E3.5":
        args = ["--config", str(config_path), "--output", str(output)]
        if mode:
            args.extend(["--mode", mode])
        _run_subprocess(script_dir / "run_fault_recovery_bench.py", args)
        return

    if workload == "irreversible_gate" or experiment == "E3.7":
        args = ["--config", str(config_path), "--output", str(output)]
        if mode:
            args.extend(["--mode", mode])
        _run_subprocess(script_dir / "run_irreversible_bench.py", args)
        return

    if workload in {"webarena", "theagentcompany", "taubench", "tau2", "osworld"}:
        args = ["--config", str(config_path), "--output", str(output)]
        if mode:
            args.extend(["--mode", mode])
        _run_subprocess(script_dir / "run_workload_experiment.py", args)
        return

    manifest_path = manifest or output.with_suffix(".manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "status": "unsupported",
                "reason": "No dispatcher route for config",
                "config": str(config_path),
                "experiment": config.get("experiment"),
                "workload": config.get("workload"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    payload = {
        "status": "unsupported",
        "manifest": str(manifest_path),
        "experiment": config.get("experiment"),
        "workload": config.get("workload"),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Experiment config JSON")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument(
        "--manifest",
        help="Optional manifest JSON path to append; omitted for standalone runs",
    )
    parser.add_argument(
        "--mode",
        help="Optional mode override (Tx-Full, No-Frontier, No-Tx)",
    )
    args = parser.parse_args()

    run(
        Path(args.config),
        Path(args.output),
        Path(args.manifest) if args.manifest else None,
        args.mode,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
