#!/usr/bin/env python3
"""Run external workload experiments (WebArena, TheAgentCompany, tau2-bench)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List


def _append_args(cmd: List[str], args: Dict[str, Any]) -> None:
    for key, value in args.items():
        flag = f"--{key}"
        if isinstance(value, bool):
            if value:
                cmd.append(flag)
            continue
        if isinstance(value, list):
            for item in value:
                cmd.extend([flag, str(item)])
            continue
        if value is not None:
            cmd.extend([flag, str(value)])


def _resolve_python(default: str) -> str:
    data_root = os.environ.get("DATA_ROOT", "./data")
    venv_python = Path(data_root) / ".atomix-setup-venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return default


def _run_command(
    command: List[str],
    workdir: Path | None,
    env: Dict[str, str],
    log_prefix: Path | None,
) -> Dict[str, Any]:
    start = time.time()
    stdout_path = None
    stderr_path = None
    if log_prefix:
        stdout_path = log_prefix.with_suffix(".stdout.log")
        stderr_path = log_prefix.with_suffix(".stderr.log")
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        with (
            stdout_path.open("w", encoding="utf-8") as stdout_file,
            stderr_path.open("w", encoding="utf-8") as stderr_file,
        ):
            result = subprocess.run(
                command,
                cwd=workdir,
                env=env,
                check=False,
                stdout=stdout_file,
                stderr=stderr_file,
            )
    else:
        result = subprocess.run(command, cwd=workdir, env=env, check=False)
    end = time.time()
    return {
        "command": command,
        "workdir": str(workdir) if workdir else None,
        "returncode": result.returncode,
        "started_at": start,
        "ended_at": end,
        "duration_s": end - start,
        "stdout_log": str(stdout_path) if stdout_path else None,
        "stderr_log": str(stderr_path) if stderr_path else None,
    }


def _data_path(subdir: str) -> str:
    data_root = os.environ.get("DATA_ROOT", "./data")
    return str(Path(data_root) / subdir)


def _run_webarena(config: Dict[str, Any], output: Path, mode: str) -> Dict[str, Any]:
    runner = config.get("runner", {})
    env_config = {k: str(v) for k, v in config.get("env", {}).items()}
    use_real_env = env_config.get(
        "WEBARENA_USE_REAL_ENV", os.environ.get("WEBARENA_USE_REAL_ENV", "1")
    )
    if use_real_env.strip().lower() in {"0", "false", "no"}:
        allow_mock = bool(config.get("allow_mock")) or str(
            config.get("run_id", "")
        ).startswith("smoke")
        if not allow_mock:
            raise RuntimeError(
                "WEBARENA_USE_REAL_ENV=0 is only allowed for smoke configs or "
                "configs with allow_mock=true"
            )
        start = time.time()
        return {
            "command": ["mock-webarena"],
            "workdir": None,
            "returncode": 0,
            "started_at": start,
            "ended_at": start,
            "duration_s": 0.0,
            "stdout_log": None,
            "stderr_log": None,
            "mock": True,
            "mode": mode,
        }
    repo_root = Path(__file__).resolve().parents[1]
    webarena_repo = repo_root / "workloads" / "webarena"
    default_workdir = os.environ.get("WEBARENA_DATA_DIR", _data_path("webarena"))
    workdir = Path(
        runner.get(
            "workdir", str(webarena_repo) if webarena_repo.exists() else default_workdir
        )
    ).resolve()
    script_value = runner.get("script", "run.py")
    script_path = Path(script_value)
    if not script_path.is_absolute():
        base = webarena_repo if webarena_repo.exists() else workdir
        script_path = base / script_value
    script = str(script_path)
    base_result_dir = config.get("result_dir", "results/webarena")
    mode_suffix = mode.lower().replace("-", "_")
    atomix_root = Path(__file__).resolve().parents[1]
    base_path = atomix_root / base_result_dir
    result_dir = f"{base_path}_{mode_suffix}"
    args = dict(runner.get("args", {}))
    args.pop("no_frontier", None)
    args.pop("no_atomix", None)
    args.pop("atomix_mode", None)
    args["result_dir"] = result_dir
    args.setdefault("atomix_mode", mode)
    venv_python = _resolve_python("python")
    cmd = [venv_python, script]

    _append_args(cmd, args)
    env = dict(os.environ)
    env.update(env_config)
    env.setdefault("WEBARENA_DATA_DIR", default_workdir)
    # WebArena requires its root in PYTHONPATH for internal imports
    existing_pythonpath = env.get("PYTHONPATH", "")
    webarena_pythonpath = str(workdir)
    if existing_pythonpath:
        env["PYTHONPATH"] = f"{webarena_pythonpath}:{existing_pythonpath}"
    else:
        env["PYTHONPATH"] = webarena_pythonpath
    # WebArena's run.py spawns subprocesses with bare "python" - ensure venv is in PATH
    venv_bin = str(Path(venv_python).parent)
    existing_path = env.get("PATH", "")
    env["PATH"] = f"{venv_bin}:{existing_path}"
    return _run_command(cmd, Path(default_workdir).resolve(), env, output)


def _run_theagentcompany(
    config: Dict[str, Any], output: Path, mode: str
) -> Dict[str, Any]:
    runner = config.get("runner", {})
    root = Path(
        runner.get(
            "workdir",
            os.environ.get("THEAGENTCOMPANY_DIR", _data_path("theagentcompany")),
        )
    ).resolve()
    script = runner.get("script", "evaluation/run_eval.sh")
    cmd = ["bash", script]
    _append_args(cmd, runner.get("args", {}))
    env = dict(os.environ)
    env.update({k: str(v) for k, v in config.get("env", {}).items()})
    env.setdefault("PYTHONPATH", str(root))
    return _run_command(cmd, root, env, output)


def _run_taubench(config: Dict[str, Any], output: Path, mode: str) -> Dict[str, Any]:
    runner = config.get("runner", {})
    repo_root = Path(__file__).resolve().parents[1]
    tau2_wrapper = repo_root / "workloads" / "tau2" / "run_atomix.py"
    root = Path(
        runner.get("workdir", os.environ.get("TAUBENCH_DIR", _data_path("tau2-bench")))
    ).resolve()
    script = runner.get("script", str(tau2_wrapper))
    args = dict(runner.get("args", {}))
    args.setdefault("mode", mode)
    tau2_python = root / ".venv" / "bin" / "python"
    cmd = [
        str(tau2_python if tau2_python.exists() else _resolve_python("python")),
        script,
    ]
    _append_args(cmd, args)
    env = dict(os.environ)
    env.update({k: str(v) for k, v in config.get("env", {}).items()})
    env["PYTHONPATH"] = f"{root}/src"
    env["PYTHONNOUSERSITE"] = "1"
    return _run_command(cmd, root, env, output)


def _run_osworld(config: Dict[str, Any], output: Path, mode: str) -> Dict[str, Any]:
    runner = config.get("runner", {})
    workdir = Path(runner.get("workdir", ".")).resolve()
    script = runner.get("script", "scripts/run_osworld_atomix.py")
    args = runner.get("args", {})
    # Set default osworld-path from env if not specified
    if "osworld-path" not in args:
        args["osworld-path"] = os.environ.get("OSWORLD_DATA_DIR", _data_path("osworld"))
    if mode == "No-Tx":
        args["baseline"] = True
    elif mode == "No-Frontier":
        args["no-frontier"] = True
    args.setdefault("output-json", str(output.with_suffix(".child.jsonl")))
    cmd = [_resolve_python("python"), script]
    _append_args(cmd, args)
    env = dict(os.environ)
    env.update({k: str(v) for k, v in config.get("env", {}).items()})
    env.setdefault("PYTHONPATH", f"{workdir}/src:{workdir}")
    summary = _run_command(cmd, workdir, env, output)
    child_success = _read_child_success(Path(args["output-json"]))
    if child_success is not None:
        summary["child_success"] = child_success
        if not child_success:
            summary["returncode"] = summary.get("returncode") or 1
    return summary


def _read_child_success(path: Path) -> bool | None:
    if not path.exists():
        return None
    successes: list[bool] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "success" in payload:
            successes.append(bool(payload["success"]))
    if not successes:
        return None
    return all(successes)


def run(config_path: Path, output: Path, mode_override: str | None = None) -> bool:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if mode_override:
        config["mode"] = mode_override
    workload = str(config.get("workload", ""))
    mode = str(config.get("mode", "Tx-Full"))

    output.parent.mkdir(parents=True, exist_ok=True)

    if workload == "webarena":
        summary = _run_webarena(config, output, mode)
    elif workload == "theagentcompany":
        summary = _run_theagentcompany(config, output, mode)
    elif workload in {"taubench", "tau2"}:
        summary = _run_taubench(config, output, mode)
    elif workload == "osworld":
        summary = _run_osworld(config, output, mode)
    else:
        raise ValueError(f"Unsupported workload: {workload}")

    payload = {
        "experiment": config.get("experiment"),
        "workload": workload,
        "mode": mode,
        "run_id": config.get("run_id"),
        "summary": summary,
        "success": summary.get("returncode", 1) == 0,
    }
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return bool(payload["success"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Experiment config JSON")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--mode", help="Optional mode override")
    args = parser.parse_args()

    return 0 if run(Path(args.config), Path(args.output), args.mode) else 1


if __name__ == "__main__":
    raise SystemExit(main())
