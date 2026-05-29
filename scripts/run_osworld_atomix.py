#!/usr/bin/env python3
"""
Run OSWorld tasks with Atomix transactional semantics.

Usage:
    python run_osworld_atomix.py --provider docker --task <task-id>
    python run_osworld_atomix.py --demo
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add paths
SCRIPT_DIR = Path(__file__).parent
ATOMIX_ROOT = SCRIPT_DIR.parent

sys.path.insert(0, str(ATOMIX_ROOT / "src"))


def _resolve_osworld_root() -> Path:
    env_root = os.environ.get("OSWORLD_DATA_DIR") or os.environ.get("OSWORLD_ROOT")
    if env_root:
        return Path(env_root).expanduser()
    data_root = Path(
        os.environ.get("DATA_ROOT", str(ATOMIX_ROOT / "data"))
    ).expanduser()
    data_candidate = data_root / "osworld"
    if data_candidate.exists():
        return data_candidate
    raise FileNotFoundError(
        "OSWorld repo not found. Set OSWORLD_DATA_DIR/OSWORLD_ROOT or populate "
        "DATA_ROOT/osworld (via scripts/download_data.sh)."
    )


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("atomix.osworld_runner")

from atomix.injector import FaultProfile
from atomix.integrations.workloads.osworld.real.vm_client import VMConfig
from atomix.integrations.workloads.osworld.real.agent import ClaudeAgent, ScriptedAgent
from atomix.integrations.workloads.osworld.real.harness import (
    RealOSWorldHarness,
    RealOSWorldTask,
)
from atomix.integrations.workloads.osworld.real.tasks.task_loader import TaskLoader


def _load_scripted_actions(path: Optional[str]) -> List[Dict[str, Any]]:
    if not path:
        return [{"action_type": "done", "args": {}}]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Scripted actions must be a list of action dicts")
    return data


def _load_task(osworld_path: Path, task_id: Optional[str]) -> RealOSWorldTask:
    loader = TaskLoader(osworld_path)
    if task_id:
        task = loader.load_task_by_id(task_id)
        if not task:
            task_file = (
                Path(osworld_path)
                / "evaluation_examples"
                / "examples"
                / "chrome"
                / f"{task_id}.json"
            )
            task = loader._load_task_file(task_file, "chrome", task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")
        return task
    tasks = loader.load_all_tasks()
    if not tasks:
        raise ValueError("No OSWorld tasks found")
    return tasks[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OSWorld with Atomix")
    parser.add_argument(
        "--provider",
        default="docker",
        choices=["docker", "vmware", "virtualbox", "aws"],
    )
    parser.add_argument("--path-to-vm", help="Path to the VM image (VMware/VirtualBox)")
    parser.add_argument("--snapshot", default="init_state")
    parser.add_argument("--region", help="Cloud region")
    parser.add_argument("--headless", action="store_true", default=False)
    parser.add_argument("--require-a11y-tree", action="store_true", default=False)
    parser.add_argument("--enable-proxy", action="store_true", default=False)
    parser.add_argument("--client-password", default="")
    parser.add_argument("--os-type", default="Ubuntu")
    parser.add_argument("--osworld-path", default=None)
    parser.add_argument("--task", help="Task ID to run")
    parser.add_argument("--demo", action="store_true", help="Run a demo task")
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument(
        "--scripted", action="store_true", help="Use scripted agent instead of Claude"
    )
    parser.add_argument("--scripted-actions", help="JSON file with scripted actions")
    parser.add_argument("--baseline", action="store_true", help="Run without Atomix")
    parser.add_argument(
        "--no-frontier", action="store_true", help="Run Atomix without frontier gating"
    )
    parser.add_argument(
        "--mode",
        choices=[
            "Tx-Full", "CR", "No-Frontier", "No-Tx", "Tx-NoFrontier+Retry",
            "Mutex+WAL+Rollback", "TCC-Confirm", "OCC-Revalidate-and-Retry",
        ],
        default=None,
        help="Atomix mode (overrides --baseline/--no-frontier)",
    )
    parser.add_argument("--effect-log", help="Path to effect log JSONL")
    parser.add_argument("--store-path", help="SQLite store path for effects/artifacts")
    parser.add_argument("--anthropic-api-key", help="Anthropic API key override")
    parser.add_argument("--mock-vm", action="store_true", help="Use mock VM client")
    parser.add_argument(
        "--use-desktop-env",
        action="store_true",
        help="Use OSWorld DesktopEnv reset/evaluation path when available",
    )
    parser.add_argument(
        "--allow-dirty-vm",
        action="store_true",
        help="Allow VMClient runs without snapshot reset; results may accumulate state",
    )
    parser.add_argument("--vm-port", type=int, default=5000, help="VM server port")
    parser.add_argument("--vm-host", default="localhost", help="VM server host")
    parser.add_argument("--output-json", help="Write result JSON to this path")
    parser.add_argument(
        "--fault-probability",
        type=float,
        default=0.0,
        help="Probability of injected tool failure",
    )
    parser.add_argument(
        "--fault-duplicate",
        type=float,
        default=0.0,
        help="Probability of injected duplicate execution",
    )
    parser.add_argument(
        "--fault-delay-min",
        type=float,
        default=0.0,
        help="Minimum injected delay (seconds)",
    )
    parser.add_argument(
        "--fault-delay-max",
        type=float,
        default=0.0,
        help="Maximum injected delay (seconds)",
    )
    parser.add_argument(
        "--fault-after-apply",
        action="store_true",
        help="Raise after applying action to simulate lost response",
    )
    args = parser.parse_args()

    # Resolve --mode into --baseline/--no-frontier flags
    if args.mode == "No-Tx":
        args.baseline = True
    elif args.mode == "No-Frontier":
        args.no_frontier = True
    elif args.mode == "CR" or args.mode == "Tx-NoFrontier+Retry":
        args.no_frontier = True  # no frontier gating, but retries via max_retries
    elif args.mode in {"Mutex+WAL+Rollback", "TCC-Confirm", "OCC-Revalidate-and-Retry"}:
        # New baselines from atomix.baselines. The OSWorld harness uses
        # AtomixRuntime as a thin wrapper; for these baselines we still drive
        # through AtomixRuntime but swap its TransactionManager. The mode
        # name is propagated to the harness which selects the baseline.
        pass

    if not args.demo and not args.task:
        logger.error("Specify --task or use --demo")
        return 1

    api_key = None
    if not args.scripted:
        api_key = args.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error("Set ANTHROPIC_API_KEY or use --scripted")
            return 1

    osworld_root: Optional[Path] = None
    if args.osworld_path:
        osworld_root = Path(args.osworld_path).expanduser()
    else:
        try:
            osworld_root = _resolve_osworld_root()
        except FileNotFoundError as exc:
            if not args.demo:
                logger.error(str(exc))
                return 1
            logger.warning(str(exc))

    if osworld_root:
        sys.path.insert(0, str(osworld_root))

    try:
        if args.demo and not osworld_root:
            task = RealOSWorldTask(
                id="demo",
                domain="os",
                instruction="Right-click on the desktop.",
                config_file=None,
            )
        elif args.demo:
            try:
                assert osworld_root is not None
                task = _load_task(osworld_root, None)
            except Exception:
                task = RealOSWorldTask(
                    id="demo",
                    domain="os",
                    instruction="Right-click on the desktop.",
                    config_file=None,
                )
        else:
            if not osworld_root:
                raise FileNotFoundError(
                    "OSWorld repo not found. Set OSWORLD_DATA_DIR/OSWORLD_ROOT or populate "
                    "DATA_ROOT/osworld (via scripts/download_data.sh)."
                )
            task = _load_task(osworld_root, args.task)
    except Exception as exc:
        logger.error(f"Failed to load task: {exc}")
        return 1

    if args.scripted:
        actions = _load_scripted_actions(args.scripted_actions)
        agent = ScriptedAgent(actions)
    else:
        run_id = f"osworld:{args.task or 'demo'}:{args.mode or ('baseline' if args.baseline else 'Tx-Full')}"
        agent = ClaudeAgent(api_key=api_key, run_id=run_id)

    effect_log_path = Path(args.effect_log) if args.effect_log else None
    store_path = Path(args.store_path) if args.store_path else None
    env_kwargs = {
        "provider_name": args.provider,
        "region": args.region,
        "path_to_vm": args.path_to_vm,
        "snapshot_name": args.snapshot,
        "action_space": "pyautogui",
        "headless": args.headless,
        "require_a11y_tree": args.require_a11y_tree,
        "os_type": args.os_type,
        "enable_proxy": args.enable_proxy,
        "client_password": args.client_password,
    }

    fault_profile = None
    if (
        args.fault_probability > 0
        or args.fault_duplicate > 0
        or args.fault_delay_max > 0
    ):
        fault_profile = FaultProfile(
            duplicate_probability=args.fault_duplicate,
            exception_probability=args.fault_probability,
            min_delay_s=args.fault_delay_min,
            max_delay_s=args.fault_delay_max,
        )

    vm_config = VMConfig(host=args.vm_host, port=args.vm_port)

    use_desktop = args.use_desktop_env
    if not args.mock_vm and not use_desktop and not args.allow_dirty_vm:
        logger.error(
            "Real OSWorld VMClient runs need reset support. Pass --use-desktop-env "
            "for OSWorld DesktopEnv reset/evaluation, or --allow-dirty-vm to run "
            "without reset and accept non-reproducible accumulated state."
        )
        return 2
    harness = RealOSWorldHarness(
        vm_config=vm_config,
        effect_log_path=effect_log_path,
        store_path=store_path,
        use_mock_vm=args.mock_vm,
        use_desktop_env=use_desktop,
        desktop_env_kwargs=env_kwargs if use_desktop else None,
        no_frontier=args.no_frontier,
        max_retries=3 if args.mode in ("CR", "Tx-NoFrontier+Retry") else None,
        fault_profile=fault_profile,
        fault_after_apply_prob=args.fault_probability
        if (args.fault_after_apply or args.baseline)
        else 0.0,
        osworld_path=osworld_root,
    )

    # New A1 baselines: swap the TransactionManager out for the
    # corresponding mechanism baseline. The harness uses
    # `runtime.tx_manager` only via the begin/record/commit/abort surface
    # which BaselineProtocol implements — drop-in.
    if args.mode in {"Mutex+WAL+Rollback", "TCC-Confirm", "OCC-Revalidate-and-Retry"}:
        from atomix.baselines import (
            MutexWalRollback,
            OCCRevalidateRetry,
            TCCConfirm,
        )

        def _install_baseline(harness):
            runtime = harness._setup_runtime()
            apply_effect = runtime.tx_manager.apply_effect
            wal_path = (
                Path(args.effect_log).with_suffix(".wal.jsonl")
                if args.effect_log else None
            )
            if args.mode == "Mutex+WAL+Rollback":
                runtime.tx_manager = MutexWalRollback(apply_effect, wal_path=wal_path)
            elif args.mode == "TCC-Confirm":
                runtime.tx_manager = TCCConfirm(apply_effect)
            else:
                runtime.tx_manager = OCCRevalidateRetry(apply_effect, retry_budget=3)
            return runtime

        # Patch the harness's _setup_runtime so each begin uses the swapped
        # baseline. Keep the original for fallback.
        original_setup = harness._setup_runtime
        def _patched_setup():
            runtime = original_setup()
            apply_effect = runtime.tx_manager.apply_effect
            wal_path = (
                Path(args.effect_log).with_suffix(".wal.jsonl")
                if args.effect_log else None
            )
            if args.mode == "Mutex+WAL+Rollback":
                runtime.tx_manager = MutexWalRollback(apply_effect, wal_path=wal_path)
            elif args.mode == "TCC-Confirm":
                runtime.tx_manager = TCCConfirm(apply_effect)
            else:
                runtime.tx_manager = OCCRevalidateRetry(apply_effect, retry_budget=3)
            return runtime
        harness._setup_runtime = _patched_setup  # type: ignore[assignment]

    try:
        if args.baseline:
            result = harness.run_task_baseline(task, agent, max_steps=args.max_steps)
        else:
            result = harness.run_task_with_agent(task, agent, max_steps=args.max_steps)

        logger.info("=" * 50)
        logger.info(f"Task: {result.task_id}")
        logger.info(f"Success: {result.success}")
        logger.info(f"Mode: {result.mode}")
        logger.info(f"Steps: {result.steps_taken}")
        logger.info(f"Duration: {result.duration_ms:.0f}ms")
        logger.info(f"Effects applied: {result.effects_applied}")
        logger.info(f"Effects compensated: {result.effects_compensated}")
        if result.error:
            logger.info(f"Error: {result.error}")

        # Write JSON output
        result_dict = {
            "task_id": result.task_id,
            "success": result.success,
            "mode": result.mode,
            "steps_taken": result.steps_taken,
            "duration_ms": result.duration_ms,
            "effects_applied": result.effects_applied,
            "effects_compensated": result.effects_compensated,
            "error": result.error,
        }
        if args.output_json:
            Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
            with open(args.output_json, "a") as f:
                f.write(json.dumps(result_dict) + "\n")
        print(json.dumps(result_dict, indent=2))
        return 0 if result.success else 1
    finally:
        harness.close()


if __name__ == "__main__":
    raise SystemExit(main())
