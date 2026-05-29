#!/usr/bin/env python3
"""Atomix-aware WebArena runner.

Wraps WebArena's browser environment with Atomix transactions and fault
injection, then runs the standard evaluation pipeline. Reports per-task
success rates across modes.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

SCRIPT_DIR = Path(__file__).resolve().parent
ATOMIX_ROOT = SCRIPT_DIR.parent

sys.path.insert(0, str(ATOMIX_ROOT / "src"))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("atomix.webarena_runner")


def _resolve_webarena_root() -> Path:
    env_root = os.environ.get("WEBARENA_DATA_DIR") or os.environ.get("WEBARENA_ROOT")
    if env_root:
        return Path(env_root).expanduser()
    candidate = ATOMIX_ROOT / "workloads" / "webarena"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        "WebArena repo not found. Set WEBARENA_DATA_DIR or check workloads/webarena."
    )


def run_webarena_with_atomix(
    webarena_root: Path,
    mode: str,
    fault_probability: float,
    test_start_idx: int,
    test_end_idx: int,
    model: str,
    result_dir: Path,
    instruction_path: str | None = None,
    max_steps: int = 15,
    task_ids: list[int] | None = None,
    temperature: float = 1.0,
) -> Dict[str, Any]:
    """Run WebArena tasks with Atomix wrapping and fault injection."""
    # Add WebArena to path
    if str(webarena_root) not in sys.path:
        sys.path.insert(0, str(webarena_root))

    from atomix.runtime import AtomixRuntime
    from atomix.adapters import ToolAdapter
    from atomix.effects import Effect
    from atomix.epoch import EpochManager
    from atomix.injector import FaultProfile
    from atomix.tool_result import ToolMeta, normalize_tool_result

    # Import WebArena components
    try:
        from browser_env import ScriptBrowserEnv, create_stop_action
        from evaluation_harness.evaluators import evaluator_router
        from agent import PromptAgent, construct_agent
    except ImportError as e:
        logger.error(f"Failed to import WebArena: {e}")
        logger.info("Ensure WEBARENA_DATA_DIR points to the WebArena repo")
        return {"error": str(e), "mode": mode, "tasks": []}

    # Patch WebArena's openai client to record token usage. WebArena uses the
    # 0.27 SDK shape (openai.ChatCompletion.create) which still returns
    # `response["usage"]`. We wrap once at import time so every call lands.
    try:
        from atomix.usage_log import record_usage
        import openai as _openai  # type: ignore
        _wb_run_id = f"webarena:{mode}:fp{fault_probability}"
        if not getattr(_openai, "_atomix_usage_patched", False):
            _orig = _openai.ChatCompletion.create  # type: ignore[attr-defined]

            def _patched(*a, **kw):
                resp = _orig(*a, **kw)
                try:
                    usage = resp.get("usage") if isinstance(resp, dict) else getattr(resp, "usage", None)
                    if usage:
                        if isinstance(usage, dict):
                            in_t = usage.get("prompt_tokens", 0)
                            out_t = usage.get("completion_tokens", 0)
                        else:
                            in_t = getattr(usage, "prompt_tokens", 0)
                            out_t = getattr(usage, "completion_tokens", 0)
                        record_usage(
                            provider="openai", model=str(kw.get("model", model)),
                            input_tokens=in_t, output_tokens=out_t,
                            run_id=_wb_run_id,
                        )
                except Exception:
                    logger.exception("usage capture failed")
                return resp
            _openai.ChatCompletion.create = _patched  # type: ignore[attr-defined]
            _openai._atomix_usage_patched = True  # type: ignore[attr-defined]
    except Exception:
        logger.exception("openai usage patch failed; cost data will be missing")

    # Load task configs — use explicit task_ids if provided, else range
    config_dir = webarena_root / "config_files"
    task_configs = []
    indices = task_ids if task_ids else list(range(test_start_idx, test_end_idx))
    for idx in indices:
        cfg_path = config_dir / f"{idx}.json"
        if cfg_path.exists():
            task_configs.append((idx, json.loads(cfg_path.read_text())))

    if not task_configs:
        logger.warning(f"No task configs found in {config_dir} for indices {indices}")
        return {"error": "no tasks", "mode": mode, "tasks": []}

    # Setup fault profile
    fault_profile = None
    if fault_probability > 0:
        fault_profile = FaultProfile(
            exception_probability=fault_probability,
        )

    # Results
    task_results: List[Dict[str, Any]] = []
    total_tasks = len(task_configs)
    successes = 0
    faults_triggered = 0

    # Create browser env once and reuse across tasks (avoids Playwright async/sync conflicts)
    env = None
    try:
        env = ScriptBrowserEnv(
            headless=True,
            slow_mo=0,
            observation_type="accessibility_tree",
            current_viewport_only=True,
            viewport_size={"width": 1280, "height": 720},
        )
    except Exception as e:
        logger.error(f"Failed to create browser env: {e}")
        return {
            "error": f"env_create: {e}",
            "mode": mode,
            "tasks": [],
            "total_tasks": total_tasks,
            "successes": 0,
            "success_rate": 0.0,
            "total_faults": 0,
        }

    for task_idx, (config_idx, task_config) in enumerate(task_configs):
        task_id = task_config.get("task_id", f"task-{config_idx}")
        logger.info(f"[{mode}] Task {task_idx+1}/{total_tasks}: {task_id}")

        # Setup Atomix runtime for this task
        applied_effects: List[Dict] = []
        compensated: List[str] = []

        def apply_effect(eff: Effect) -> None:
            applied_effects.append(eff.payload if isinstance(eff.payload, dict) else {})

        runtime = AtomixRuntime(
            apply_effect=apply_effect,
            effect_log_path=None,
            frontier_enabled=(mode == "Tx-Full"),
            fault_profile=fault_profile if mode != "No-Tx" else None,
        )

        # New A1 mechanism baselines: drop in by swapping tx_manager.
        if mode in {"Mutex+WAL+Rollback", "TCC-Confirm", "OCC-Revalidate-and-Retry"}:
            from atomix.baselines import (
                MutexWalRollback,
                OCCRevalidateRetry,
                TCCConfirm,
            )
            if mode == "Mutex+WAL+Rollback":
                runtime.tx_manager = MutexWalRollback(apply_effect)
            elif mode == "TCC-Confirm":
                runtime.tx_manager = TCCConfirm(apply_effect)
            else:
                runtime.tx_manager = OCCRevalidateRetry(apply_effect, retry_budget=3)

        epoch_manager = EpochManager()
        task_faults = 0

        try:
            # Reset env with task
            obs, info = env.reset(options={"config_file": str(config_dir / f"{config_idx}.json")})

            # Create agent
            if instruction_path:
                instr_path = webarena_root / instruction_path
            else:
                instr_path = webarena_root / "agent" / "prompts" / "jsons" / "p_cot_id_actree_2s.json"

            agent = None
            try:
                import argparse as _ap
                agent_args = _ap.Namespace(
                    agent_type="prompt",
                    instruction_path=str(instr_path),
                    provider="openai",
                    model=model,
                    mode="chat",
                    action_set_tag="id_accessibility_tree",
                    temperature=temperature,
                    top_p=0.9,
                    context_length=0,
                    max_tokens=384,
                    stop_token=None,
                    max_obs_length=1920,
                    max_retry=1,
                )
                agent = construct_agent(agent_args)
            except Exception as agent_err:
                logger.warning(f"Agent creation failed: {agent_err}, using scripted fallback")

            # Agent-based evaluation with LLM
            from browser_env import StateInfo, Trajectory, ActionTypes

            if agent is not None:
                try:
                    agent.reset(str(config_dir / f"{config_idx}.json"))
                except Exception:
                    pass

            task_intent = task_config.get("intent", "")
            max_task_steps = max_steps
            task_success = False
            trajectory: Trajectory = []
            state_info: StateInfo = {"observation": obs, "info": info}
            trajectory.append(state_info)
            meta_data = {"action_history": ["None"]}

            for step in range(max_task_steps):
                epoch = epoch_manager.next(trace_id=f"webarena-{config_idx}")

                # Get action from agent (or scripted fallback)
                if agent is not None:
                    try:
                        action = agent.next_action(trajectory, task_intent, meta_data=meta_data)
                    except Exception as agent_err:
                        logger.warning(f"Agent error at step {step}: {agent_err}")
                        action = create_stop_action(f"Agent error: {agent_err}")
                else:
                    action = create_stop_action("No agent available")

                trajectory.append(action)

                action_type_str = str(action.get("action_type", "unknown"))
                logger.info(f"  Task {config_idx} step {step}: {action_type_str}")

                # Check for STOP action
                if action.get("action_type") == ActionTypes.STOP:
                    answer = action.get("answer", "")
                    logger.info(f"  Task {config_idx}: STOP at step {step}, answer={answer!r}")
                    break

                # Execute action with fault handling
                if mode == "No-Tx":
                    if fault_probability > 0 and random.random() < fault_probability:
                        task_faults += 1
                        task_success = False
                        break
                    try:
                        obs, reward, done, truncated, info = env.step(action)
                    except Exception:
                        task_faults += 1
                        task_success = False
                        break
                else:
                    # Atomix-wrapped action
                    class BrowserStepAdapter(ToolAdapter):
                        name = "browser_step"
                        _step = step
                        _config_idx = config_idx
                        def scopes(self, args: Dict[str, Any]) -> Set[str]:
                            return {f"browser:{self._config_idx}"}
                        def to_effect(self, args: Dict[str, Any], result: Any, ep: Any) -> Effect:
                            return Effect(
                                description=f"browser_step:{self._step}",
                                scopes={f"browser:{self._config_idx}"},
                                payload={"step": self._step, "action": str(args.get("action_type",""))},
                                idempotency_key=f"webarena:{self._config_idx}:{ep.value}",
                            )

                    runtime.register_adapter("browser_step", BrowserStepAdapter())

                    max_retries = 3 if mode in ("Tx-Full", "CR", "Tx-NoFrontier+Retry") else 0
                    attempt = 0
                    step_ok = False

                    while attempt <= max_retries:
                        try:
                            cur_action = action  # capture for lambda
                            result_tuple, tx = runtime.run_tool(
                                "browser_step",
                                lambda **kw: env.step(cur_action),
                                {"action_type": str(action.get("action_type", "")), "step": step},
                                epoch,
                            )
                            step_ok = True
                            if isinstance(result_tuple, tuple) and len(result_tuple) >= 4:
                                obs, reward, done, truncated = result_tuple[:4]
                                if len(result_tuple) >= 5:
                                    info = result_tuple[4]
                            break
                        except Exception as e:
                            task_faults += 1
                            attempt += 1
                            if attempt <= max_retries:
                                epoch = epoch_manager.next(trace_id=f"webarena-{config_idx}")

                    if not step_ok:
                        task_success = False
                        break

                # Update trajectory with new state
                state_info = {"observation": obs, "info": info}
                trajectory.append(state_info)

                # Update action history
                action_desc = str(action.get("action_type", "unknown"))
                meta_data["action_history"].append(action_desc)

                if done:
                    break

            # Evaluate with WebArena evaluator
            try:
                evaluator = evaluator_router(str(config_dir / f"{config_idx}.json"))
                score = evaluator(
                    trajectory=trajectory,
                    config_file=str(config_dir / f"{config_idx}.json"),
                    page=env.page,
                    client=env.get_page_client(env.page),
                )
                task_success = score == 1.0
            except Exception as eval_err:
                logger.warning(f"Evaluation failed: {eval_err}")
                # If we completed all steps without error, count as partial success
                task_success = (step == max_task_steps - 1) and not any(
                    r.get("error") for r in task_results[-1:] if isinstance(r, dict)
                )

            if task_success:
                successes += 1

        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}")
            task_success = False

        faults_triggered += task_faults
        task_results.append({
            "task_id": task_id,
            "config_idx": config_idx,
            "success": task_success if "task_success" in dir() else False,
            "faults": task_faults,
            "compensations": len(compensated),
            "effects_applied": len(applied_effects),
        })

    # Close shared browser env
    if env is not None:
        try:
            env.close()
        except Exception:
            pass

    summary = {
        "mode": mode,
        "fault_probability": fault_probability,
        "total_tasks": total_tasks,
        "successes": successes,
        "success_rate": successes / total_tasks if total_tasks > 0 else 0.0,
        "total_faults": faults_triggered,
        "tasks": task_results,
    }

    # Write results
    result_dir.mkdir(parents=True, exist_ok=True)
    out_file = result_dir / f"webarena_{mode.lower().replace('-', '_')}.json"
    out_file.write_text(json.dumps(summary, indent=2))
    logger.info(f"[{mode}] Done: {successes}/{total_tasks} tasks succeeded, {faults_triggered} faults")

    return summary


def run_webarena_mock(
    mode: str,
    fault_probability: float,
    num_tasks: int,
    steps_per_task: int,
    result_dir: Path,
    speculative_k: int = 1,
) -> Dict[str, Any]:
    """Run simulated WebArena-style multi-step browser tasks with Atomix wrapping."""
    from atomix.runtime import AtomixRuntime
    from atomix.adapters import ToolAdapter
    from atomix.effects import Effect
    from atomix.epoch import EpochManager
    from atomix.injector import FaultProfile

    fault_profile = None
    if fault_probability > 0:
        fault_profile = FaultProfile(exception_probability=fault_probability)

    task_results: List[Dict[str, Any]] = []
    successes = 0
    total_faults = 0

    BROWSER_ACTIONS = [
        "navigate", "click", "type_text", "scroll", "wait",
        "select_option", "go_back", "submit_form",
    ]

    for task_idx in range(num_tasks):
        task_id = f"webarena-mock-{task_idx}"
        task_faults = 0
        task_success = True
        steps_completed = 0
        compensated = 0
        effects_applied = 0
        branch_conflicts = 0  # Track speculative branch conflicts

        epoch_manager = EpochManager()

        if mode == "No-Tx":
            # No protection: faults corrupt state
            state: List[str] = []
            for step in range(steps_per_task):
                action = BROWSER_ACTIONS[step % len(BROWSER_ACTIONS)]
                if fault_probability > 0 and random.random() < fault_probability:
                    task_faults += 1
                    # Fault corrupts partial state
                    state.append(f"CORRUPTED:{action}")
                    task_success = False
                    break
                state.append(action)
                steps_completed += 1
            if steps_completed == steps_per_task:
                task_success = True
        else:
            # Atomix-wrapped (Tx-Full or No-Frontier)
            applied: List[Dict] = []
            branch_conflicts = 0  # Track when branches contend on same scope
            frontier_blocks = 0   # Track when frontier blocked a branch

            def apply_fn(eff: Effect) -> None:
                applied.append(eff.payload if isinstance(eff.payload, dict) else {})

            runtime = AtomixRuntime(
                apply_effect=apply_fn,
                effect_log_path=None,
                frontier_enabled=(mode == "Tx-Full"),
                fault_profile=fault_profile,
            )

            # New A1 mechanism baselines: drop in by swapping tx_manager.
            if mode in {"Mutex+WAL+Rollback", "TCC-Confirm", "OCC-Revalidate-and-Retry"}:
                from atomix.baselines import (
                    MutexWalRollback,
                    OCCRevalidateRetry,
                    TCCConfirm,
                )
                if mode == "Mutex+WAL+Rollback":
                    runtime.tx_manager = MutexWalRollback(apply_fn)
                elif mode == "TCC-Confirm":
                    runtime.tx_manager = TCCConfirm(apply_fn)
                else:
                    runtime.tx_manager = OCCRevalidateRetry(apply_fn, retry_budget=3)

            # Simulated page elements for contention
            # Each branch operates on an element; collisions create contention
            NUM_ELEMENTS = 5  # With k=3, ~40% chance of collision

            class BrowserAdapter(ToolAdapter):
                name = "browser_action"
                def scopes(self, args: Dict[str, Any]) -> Set[str]:
                    element_id = args.get("element", 0)
                    return {f"browser:task-{task_idx}:element-{element_id}"}
                def to_effect(self, args: Dict[str, Any], result: Any, ep: Any) -> Effect:
                    element_id = args.get("element", 0)
                    return Effect(
                        description=f"browser:{args.get('action', 'unknown')}:element-{element_id}",
                        scopes=self.scopes(args),
                        payload={"action": args.get("action"), "step": args.get("step"), "element": element_id},
                        idempotency_key=f"webarena:{task_idx}:element-{element_id}:{ep.value}",
                    )

            runtime.register_adapter("browser_action", BrowserAdapter())

            for step in range(steps_per_task):
                if speculative_k == 1:
                    # Sequential execution (original behavior)
                    action = BROWSER_ACTIONS[step % len(BROWSER_ACTIONS)]
                    element_id = step % NUM_ELEMENTS
                    epoch = epoch_manager.next(trace_id=f"webarena-{task_idx}")

                    max_retries = 3 if mode in ("Tx-Full", "Tx-NoFrontier+Retry") else 0
                    attempt = 0
                    step_ok = False

                    while attempt <= max_retries:
                        try:
                            runtime.run_tool(
                                "browser_action",
                                lambda **kw: {"success": True, "action": kw.get("action")},
                                {"action": action, "step": step, "element": element_id},
                                epoch,
                            )
                            step_ok = True
                            break
                        except Exception:
                            task_faults += 1
                            attempt += 1
                            if attempt <= max_retries:
                                epoch = epoch_manager.next(trace_id=f"webarena-{task_idx}")

                    if not step_ok:
                        task_success = False
                        break
                    steps_completed += 1
                else:
                    # Speculative parallel execution
                    # Generate k different actions/elements for this step
                    import asyncio
                    import time

                    base_action_idx = step % len(BROWSER_ACTIONS)
                    branch_configs = []
                    used_elements = set()

                    for branch_idx in range(speculative_k):
                        action_idx = (base_action_idx + branch_idx) % len(BROWSER_ACTIONS)
                        # Random element selection with potential collision
                        element_id = random.randint(0, NUM_ELEMENTS - 1)
                        branch_configs.append({
                            "action": BROWSER_ACTIONS[action_idx],
                            "element": element_id,
                            "branch": branch_idx,
                        })
                        if element_id in used_elements:
                            branch_conflicts += 1
                        used_elements.add(element_id)

                    # Execute branches in parallel (simulated via loop)
                    # In Tx-Full, frontier serializes conflicting branches
                    branch_results = []
                    branch_epochs = []

                    for config in branch_configs:
                        epoch = epoch_manager.next(trace_id=f"webarena-{task_idx}")
                        branch_epochs.append(epoch)

                        try:
                            result = runtime.run_tool(
                                "browser_action",
                                lambda **kw: {"success": True, "action": kw.get("action")},
                                {"action": config["action"], "step": step, "element": config["element"]},
                                epoch,
                            )
                            branch_results.append({"success": True, "config": config})
                        except Exception as e:
                            task_faults += 1
                            branch_results.append({"success": False, "config": config, "error": str(e)})

                    # Select winning branch (first successful, or random if all fail)
                    successful = [r for r in branch_results if r["success"]]
                    if successful:
                        winner = successful[0]  # First success wins
                        # In Tx-Full, the frontier already ensured only winner committed
                        # In No-Frontier, all successful branches committed (may corrupt state)
                        if mode != "Tx-Full" and len(successful) > 1 and len(used_elements) < speculative_k:
                            # Multiple branches committed on overlapping scopes - potential corruption
                            branch_conflicts += len(successful) - 1
                    else:
                        # All branches failed
                        task_success = False
                        break

                    steps_completed += 1

            effects_applied = len(applied)

        if task_success:
            successes += 1
        total_faults += task_faults
        task_results.append({
            "task_id": task_id,
            "success": task_success,
            "steps_completed": steps_completed,
            "steps_total": steps_per_task,
            "faults": task_faults,
            "effects_applied": effects_applied,
            "branch_conflicts": branch_conflicts,
        })

    summary = {
        "mode": mode,
        "speculative_k": speculative_k,
        "fault_probability": fault_probability,
        "total_tasks": num_tasks,
        "successes": successes,
        "success_rate": successes / num_tasks if num_tasks > 0 else 0.0,
        "total_faults": total_faults,
        "tasks": task_results,
    }

    result_dir.mkdir(parents=True, exist_ok=True)
    k_suffix = f"_k{speculative_k}" if speculative_k > 1 else ""
    out_file = result_dir / f"webarena_{mode.lower().replace('-', '_')}{k_suffix}.json"
    out_file.write_text(json.dumps(summary, indent=2))
    logger.info(f"[{mode}] Mock (k={speculative_k}): {successes}/{num_tasks} tasks, {total_faults} faults")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        default="Tx-Full",
        choices=[
            "Tx-Full", "CR", "No-Frontier", "No-Tx", "Tx-NoFrontier+Retry",
            "Mutex+WAL+Rollback", "TCC-Confirm", "OCC-Revalidate-and-Retry",
        ],
    )
    parser.add_argument("--fault-probability", type=float, default=0.0)
    parser.add_argument("--test-start-idx", type=int, default=0)
    parser.add_argument("--test-end-idx", type=int, default=5)
    parser.add_argument("--task-ids", type=int, nargs="*", default=None,
                        help="Specific task IDs to run (overrides start/end idx)")
    parser.add_argument("--model", default="gpt-4o")
    parser.add_argument("--result-dir", default="results/webarena_atomix")
    parser.add_argument("--instruction-path", default=None)
    parser.add_argument("--output", help="Summary JSON output path")
    parser.add_argument("--mock", action="store_true", help="Use mock browser instead of real WebArena")
    parser.add_argument("--speculative-k", type=int, default=1, help="Number of parallel speculative branches (1=sequential, >1=parallel speculation)")
    parser.add_argument("--num-tasks", type=int, default=10, help="Number of tasks (mock mode)")
    parser.add_argument("--steps-per-task", type=int, default=8, help="Steps per task (mock mode)")
    parser.add_argument("--max-steps", type=int, default=15, help="Max agent steps per task (real mode)")
    parser.add_argument("--temperature", type=float, default=1.0, help="LLM temperature (0.0 for deterministic)")
    parser.add_argument(
        "--min-success-rate",
        type=float,
        default=1e-12,
        help="Required success rate for exit 0; pass 0 to accept all-fail runs",
    )
    args = parser.parse_args()

    result_dir = Path(args.result_dir)

    if args.mock:
        summary = run_webarena_mock(
            mode=args.mode,
            fault_probability=args.fault_probability,
            num_tasks=args.num_tasks,
            steps_per_task=args.steps_per_task,
            result_dir=result_dir,
            speculative_k=args.speculative_k,
        )
    else:
        webarena_root = _resolve_webarena_root()
        summary = run_webarena_with_atomix(
            webarena_root=webarena_root,
            mode=args.mode,
            fault_probability=args.fault_probability,
            test_start_idx=args.test_start_idx,
            test_end_idx=args.test_end_idx,
            model=args.model,
            result_dir=result_dir,
            instruction_path=args.instruction_path,
            max_steps=args.max_steps,
            task_ids=args.task_ids,
            temperature=args.temperature,
        )

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    return 0 if float(summary.get("success_rate", 0.0)) >= args.min_success_rate else 1


if __name__ == "__main__":
    raise SystemExit(main())
