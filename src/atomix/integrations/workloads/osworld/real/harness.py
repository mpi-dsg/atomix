"""
Real OSWorld harness for running tasks with Atomix transactional semantics.

Orchestrates the VM client, Atomix runtime, and agent to execute OSWorld tasks.
"""

from __future__ import annotations

import copy
import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from atomix.effects import Effect
from atomix.injector import FaultProfile
from atomix.runtime import AtomixRuntime
from atomix.store import SqliteStore
from atomix.transactions import EffectAppliedButUnacknowledged
from atomix.tool_result import ArtifactRef

from .adapters import ALL_ADAPTERS
from .compensation import CompensationManager
from .state_tracker import StateTracker
from .vm_client import DesktopEnvClient, VMClient, VMConfig, MockVMClient

logger = logging.getLogger(__name__)


class AgentProtocol(Protocol):
    """Protocol for agents that decide actions."""

    def decide_action(
        self, screenshot: bytes, instruction: str, history: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Decide the next action based on screenshot and instruction.

        Returns:
            Dict with 'action_type' and 'args' keys
        """
        ...


@dataclass
class RealOSWorldTask:
    """Task definition for real OSWorld."""

    id: str
    domain: str  # e.g., "libreoffice", "chrome"
    instruction: str  # Natural language instruction
    config_file: Optional[Path] = None
    evaluation_fn: Optional[Callable[[bytes], bool]] = None


@dataclass
class RealTaskResult:
    """Result from running a real OSWorld task."""

    task_id: str
    success: bool
    mode: str  # "atomix" or "baseline"
    duration_ms: float
    steps_taken: int
    effects_applied: int
    effects_compensated: int
    final_screenshot: Optional[bytes] = None
    error: Optional[str] = None
    action_log: List[Dict[str, Any]] = field(default_factory=list)
    partial_state: bool = False


class RealOSWorldHarness:
    """Harness for running real OSWorld tasks with Atomix."""

    def __init__(
        self,
        vm_config: Optional[VMConfig] = None,
        effect_log_path: Optional[Path] = None,
        store_path: Optional[Path] = None,
        fault_profile: Optional[FaultProfile] = None,
        use_mock_vm: bool = False,
        use_desktop_env: bool = False,
        desktop_env_kwargs: Optional[Dict[str, Any]] = None,
        desktop_env: Optional[Any] = None,
        no_frontier: bool = False,
        max_retries: Optional[int] = None,
        fault_after_apply_prob: float = 0.0,
        osworld_path: Optional[Path] = None,
    ):
        self.vm_config = vm_config or VMConfig()
        self.fault_profile = fault_profile
        self.effect_log_path = effect_log_path
        self.store = SqliteStore(Path(store_path)) if store_path else None
        self._runtime: Optional[AtomixRuntime] = None
        self.osworld_path = osworld_path

        self._desktop_env = desktop_env
        self._use_desktop_env = use_desktop_env or desktop_env is not None
        self._no_frontier = no_frontier
        self._max_retries = max_retries
        self._fault_after_apply_prob = max(0.0, fault_after_apply_prob)
        if self._use_desktop_env:
            if self._desktop_env is None:
                try:
                    from desktop_env.desktop_env import DesktopEnv
                except ImportError as exc:
                    raise ImportError(
                        "OSWorld desktop_env not available; install OSWorld or disable desktop env"
                    ) from exc

                env_kwargs = dict(desktop_env_kwargs or {})
                env_kwargs.setdefault("action_space", "pyautogui")
                self._desktop_env = DesktopEnv(**env_kwargs)
            self.vm_client = DesktopEnvClient(self._desktop_env)
        else:
            if use_mock_vm:
                self.vm_client = MockVMClient(self.vm_config)
            else:
                self.vm_client = VMClient(self.vm_config)

        self.state_tracker = StateTracker()
        self.compensation_manager = CompensationManager(self.vm_client)

        self._adapter_cache = {
            name: adapter_cls() for name, adapter_cls in ALL_ADAPTERS.items()
        }
        self._last_effect_result: Optional[Dict[str, Any]] = None

        self._applied_effects: List[Effect] = []
        self._compensated_count = 0

    def _apply_effect(self, effect: Effect) -> None:
        """Effect application callback."""
        self._applied_effects.append(effect)
        payload = effect.payload
        action_type = payload.get("action_type")
        action_args = payload.get("args", {}) if isinstance(payload, dict) else {}
        if not action_type:
            return

        adapter = self._adapter_cache.get(action_type)
        if adapter is None:
            return

        action = adapter.build_action(action_args)
        result = self.vm_client.execute_action(action)
        if not result.success:
            raise RuntimeError(result.error or "VM action failed")
        if (
            self._fault_after_apply_prob > 0
            and random.random() < self._fault_after_apply_prob
        ):
            raise EffectAppliedButUnacknowledged("Injected fault after apply")
        self._last_effect_result = {
            "success": result.success,
            "response": result.response,
            "before_screenshot": result.before_screenshot,
            "after_screenshot": result.after_screenshot,
            "error": result.error,
        }

        payload["response"] = result.response
        payload["has_before_screenshot"] = result.before_screenshot is not None
        payload["has_after_screenshot"] = result.after_screenshot is not None

        if self.store:
            artifacts: Dict[str, Dict[str, Any]] = {}
            if result.before_screenshot:
                before_ref = ArtifactRef.from_bytes(
                    "before_screenshot",
                    result.before_screenshot,
                    content_type="image/png",
                )
                self.store.save_artifact(before_ref)
                artifacts["before_screenshot"] = {
                    "sha256": before_ref.sha256,
                    "size": before_ref.size,
                    "kind": before_ref.kind,
                    "content_type": before_ref.content_type,
                }
            if result.after_screenshot:
                after_ref = ArtifactRef.from_bytes(
                    "after_screenshot",
                    result.after_screenshot,
                    content_type="image/png",
                )
                self.store.save_artifact(after_ref)
                artifacts["after_screenshot"] = {
                    "sha256": after_ref.sha256,
                    "size": after_ref.size,
                    "kind": after_ref.kind,
                    "content_type": after_ref.content_type,
                }
            if artifacts:
                payload["artifacts"] = artifacts

        if effect.compensation:
            compensation_info = effect.compensation()
            if compensation_info is None:
                effect.compensation = None
            else:
                app_context = action_args.get("app_context", "unknown")

                def compensation(
                    info=compensation_info,
                    args=action_args,
                    before=result.before_screenshot,
                    after=result.after_screenshot,
                    app=app_context,
                    action=action_type,
                ) -> bool:
                    success = self.compensation_manager.compensate(
                        action_type=action,
                        action_args=args,
                        app_context=app,
                        compensation_info=info,
                        before_screenshot=before,
                        after_screenshot=after,
                    )
                    if success:
                        self._compensated_count += 1
                    return success

                effect.compensation = compensation

    def _setup_runtime(self) -> AtomixRuntime:
        """Create and configure Atomix runtime with all adapters."""
        runtime = AtomixRuntime(
            apply_effect=self._apply_effect,
            effect_log_path=str(self.effect_log_path) if self.effect_log_path else None,
            fault_profile=self.fault_profile,
            store=self.store,
            frontier_enabled=not self._no_frontier,
            recovery_policy="fail_closed",
        )

        for name, adapter in self._adapter_cache.items():
            runtime.register_adapter(name, adapter)

        self._runtime = runtime
        return runtime

    def _safe_eval(self, task: RealOSWorldTask, screenshot: bytes) -> tuple[bool, dict]:
        """
        Normalize different eval_fn return styles into (success: bool, details: dict).
        The evaluator (task) decides success, not the agent saying "done".
        """
        if task.evaluation_fn is None:
            return False, {"evaluator": "none"}

        try:
            # Try calling with screenshot
            r = task.evaluation_fn(screenshot)
        except TypeError:
            try:
                # Try calling without args
                r = task.evaluation_fn()
            except Exception as e:
                logger.warning(f"Evaluation function failed: {e}")
                return False, {"error": str(e)}
        except Exception as e:
            logger.warning(f"Evaluation failed: {e}")
            return False, {"error": str(e)}

        # Normalize result
        if isinstance(r, bool):
            return r, {"raw": r}
        if isinstance(r, dict):
            # Common patterns: {"success": bool, ...} or {"score": float, ...}
            if "success" in r:
                return bool(r["success"]), r
            if "score" in r:
                return (float(r["score"]) >= 1.0), r
            # Fallback: treat truthiness as success
            return bool(r), r

        # Fallback for other types (namedtuple/obj)
        success = getattr(r, "success", None)
        if success is not None:
            return bool(success), {"raw": r}
        score = getattr(r, "score", None)
        if score is not None:
            return float(score) >= 1.0, {"raw": r}

        return False, {"raw": r}

    def _reset_task(self, task: RealOSWorldTask) -> None:
        """Reset VM to initial state for the task. Critical for reproducible baselines."""
        if self._desktop_env:
            # Use DesktopEnv if available
            if task.config_file and task.config_file.exists():
                with open(task.config_file, encoding="utf-8") as f:
                    task_config = json.load(f)
                task_config.setdefault("instruction", task.instruction)
            else:
                task_config = {
                    "id": task.id,
                    "instruction": task.instruction,
                    "config": [],
                }
            self._desktop_env.reset(task_config=task_config)
            return

        # VMClient path: try to use OSWorld's setup mechanism
        if self.osworld_path:
            # Try to load and execute OSWorld's setup.py for this task
            examples_dir = Path(self.osworld_path) / "evaluation_examples" / "examples"
            if examples_dir.exists():
                # Find task config file
                task_files = list(examples_dir.rglob(f"{task.id}*.json"))
                if task_files:
                    config_file = task_files[0]
                    logger.info(f"Loading OSWorld task config from {config_file}")
                    # Store config_file for later use in evaluation
                    task.config_file = config_file

        # For VMClient without DesktopEnv, we can't do a full reset
        # The VM server would need to support snapshot restore
        logger.warning(
            f"VM reset for task {task.id} is incomplete - no DesktopEnv and "
            "no VM snapshot restore available. Tasks may accumulate state."
        )

    def execute_action(
        self,
        runtime: AtomixRuntime,
        action_type: str,
        args: Dict[str, Any],
        trace_id: str,
        branch_id: Optional[str] = None,
    ) -> tuple[Any, Any]:
        """Execute a single action through Atomix."""
        # Get context from VM for scope resolution
        window_info = self.vm_client.get_active_window()
        context = {
            "active_window_title": window_info.get("title", ""),
            "active_app": window_info.get("app", "unknown"),
        }
        args["app_context"] = context["active_app"]

        # Generate epoch
        epoch = runtime.epochs.next(trace_id=trace_id, branch_id=branch_id)

        # Get adapter
        adapter = runtime.adapters.get(action_type)
        if not adapter:
            raise ValueError(f"Unknown action type: {action_type}")

        # Define tool function that plans the action without side effects
        def tool_fn(**kwargs):
            return {"planned": True}

        # Run through Atomix
        self._last_effect_result = None
        result, tx = runtime.run_tool(action_type, tool_fn, args, epoch)

        # Advance frontier for this action's scopes to trigger commit
        if not self._no_frontier:
            scopes = adapter.scopes(args)
            runtime.advance_frontier(scopes, epoch)

        # Record state snapshot after effect application
        after_screenshot = None
        if isinstance(self._last_effect_result, dict):
            after_screenshot = self._last_effect_result.get("after_screenshot")
        if after_screenshot is None:
            after_screenshot = self.vm_client.screenshot()

        self.state_tracker.record(
            screenshot=after_screenshot,
            active_window=context["active_window_title"],
            active_app=context["active_app"],
            epoch_value=epoch.value,
            action_description=f"{action_type}:{args}",
            action_type=action_type,
            action_args=args,
        )

        if self._last_effect_result is not None:
            return self._last_effect_result, tx
        return result, tx

    def run_task_with_agent(
        self,
        task: RealOSWorldTask,
        agent: AgentProtocol,
        max_steps: int = 50,
    ) -> RealTaskResult:
        """Run a task using an agent that decides actions.

        CRITICAL: Success is determined by task.evaluation_fn, NOT by agent saying "done".
        The evaluator (task) decides when the task is complete, not the agent.

        Args:
            task: The task to execute
            agent: Agent that decides actions based on screenshots
            max_steps: Maximum steps before timeout
        """
        self._applied_effects = []
        self._compensated_count = 0
        self.state_tracker.clear()
        self._reset_task(task)

        # Reset agent conversation history for new task
        reset_fn = getattr(agent, "reset", None)
        if callable(reset_fn):
            reset_fn()

        runtime = self._setup_runtime()
        trace_id = f"osworld:{task.id}"

        start_time = time.time()
        error: Optional[str] = None
        action_log: List[Dict[str, Any]] = []
        step = 0
        done = False
        terminated_by = "max_steps"

        try:
            while step < max_steps and not done:
                # Get current screenshot
                screenshot = self.vm_client.screenshot() or b""

                # ALWAYS evaluate after each step - the task decides success
                success, _eval_details = self._safe_eval(task, screenshot)
                if success:
                    terminated_by = "evaluator"
                    done = True
                    logger.info(f"Task {task.id}: evaluator returned True at step {step}")
                    break

                # Ask agent for next action
                history = self.state_tracker.get_action_history()
                agent_response = agent.decide_action(
                    screenshot, task.instruction, history
                )

                action_type_raw = agent_response.get("action_type")
                action_type = (
                    str(action_type_raw).lower() if action_type_raw is not None else ""
                )
                action_args = agent_response.get("args", {}) or {}

                # Log the action
                action_log.append(
                    {
                        "step": step,
                        "action_type": action_type,
                        "args": action_args,
                    }
                )

                logger.debug(f"Step {step}: {action_type} {action_args}")

                # Check for terminal actions
                if action_type == "done":
                    # Agent says done - let evaluator decide
                    terminated_by = "agent_done_eval"
                    done = True
                    # Don't break yet - evaluate one more time below
                elif action_type == "fail":
                    error = action_args.get("reason", "Agent reported failure")
                    result, _ = self.execute_action(
                        runtime, "fail", action_args, trace_id
                    )
                    if (
                        isinstance(result, dict)
                        and result.get("success") is False
                        and error is None
                    ):
                        error = result.get("error") or "VM action failed"
                    terminated_by = "agent_fail"
                    break
                else:
                    # Execute the action with retry on transient faults
                    max_retries = self._max_retries if self._max_retries is not None else (3 if not self._no_frontier else 0)
                    attempt = 0
                    while True:
                        try:
                            result, _ = self.execute_action(
                                runtime, action_type, action_args, trace_id
                            )
                            if isinstance(result, dict) and result.get("success") is False:
                                error = result.get("error") or "VM action failed"
                            break
                        except Exception as action_err:
                            attempt += 1
                            if attempt > max_retries:
                                error = str(action_err)
                                logger.warning(
                                    f"Step {step}: action failed after {attempt} attempts: {action_err}"
                                )
                                break
                            logger.info(
                                f"Step {step}: fault detected, retrying ({attempt}/{max_retries}): {action_err}"
                            )

                if error is not None:
                    terminated_by = "error"
                    break

                step += 1

        except Exception as e:
            error = str(e)
            terminated_by = "exception"
            logger.error(f"Task {task.id} failed: {e}")

        duration_ms = (time.time() - start_time) * 1000

        # Get final screenshot and do final evaluation
        final_screenshot = self.vm_client.screenshot()
        final_success, _final_eval_details = self._safe_eval(task, final_screenshot)

        # Combine: success if evaluator says so, regardless of how we terminated
        # (unless there was an error that prevented execution)
        if error is None:
            success = final_success
        else:
            success = False

        partial = error is not None and len(self._applied_effects) > 0

        logger.info(
            f"Task {task.id}: success={success}, terminated_by={terminated_by}, "
            f"steps={step}, error={error}"
        )

        return RealTaskResult(
            task_id=task.id,
            success=success,
            mode="atomix",
            duration_ms=duration_ms,
            steps_taken=step,
            effects_applied=len(self._applied_effects),
            effects_compensated=self._compensated_count,
            final_screenshot=final_screenshot,
            error=error,
            action_log=action_log,
            partial_state=partial,
        )

    def run_task_baseline(
        self,
        task: RealOSWorldTask,
        agent: AgentProtocol,
        max_steps: int = 50,
    ) -> RealTaskResult:
        """Run a task without Atomix (baseline mode).

        CRITICAL: Success is determined by task.evaluation_fn, NOT by agent saying "done".
        """
        self.state_tracker.clear()
        self._reset_task(task)

        # Reset agent conversation history for new task
        reset_fn = getattr(agent, "reset", None)
        if callable(reset_fn):
            reset_fn()

        start_time = time.time()
        error: Optional[str] = None
        action_log: List[Dict[str, Any]] = []
        step = 0
        done = False
        effects_count = 0
        partial = False
        terminated_by = "max_steps"

        try:
            while step < max_steps and not done:
                screenshot = self.vm_client.screenshot() or b""

                # ALWAYS evaluate - the task decides success
                success, _eval_details = self._safe_eval(task, screenshot)
                if success:
                    terminated_by = "evaluator"
                    done = True
                    logger.info(f"Baseline task {task.id}: evaluator returned True at step {step}")
                    break

                history = self.state_tracker.get_action_history()
                agent_response = agent.decide_action(
                    screenshot, task.instruction, history
                )

                action_type_raw = agent_response.get("action_type")
                action_type = (
                    str(action_type_raw).lower() if action_type_raw is not None else ""
                )
                action_args = agent_response.get("args", {}) or {}

                action_log.append(
                    {
                        "step": step,
                        "action_type": action_type,
                        "args": action_args,
                    }
                )

                if action_type == "done":
                    terminated_by = "agent_done_eval"
                    done = True
                elif action_type == "fail":
                    error = action_args.get("reason", "Agent reported failure")
                    terminated_by = "agent_fail"
                    break
                else:
                    # Execute directly without Atomix
                    from .adapters import ALL_ADAPTERS

                    adapter_cls = ALL_ADAPTERS.get(action_type)
                    if not adapter_cls:
                        raise ValueError(
                            f"Unknown action type in baseline: {action_type}"
                        )

                    adapter = adapter_cls()
                    action = adapter.build_action(action_args)
                    self.vm_client.execute_action(action)
                    if (
                        self._fault_after_apply_prob > 0
                        and random.random() < self._fault_after_apply_prob
                    ):
                        raise RuntimeError("Injected fault after apply")
                    effects_count += 1

                    # Record state
                    window_info = self.vm_client.get_active_window()
                    self.state_tracker.record(
                        screenshot=self.vm_client.screenshot(),
                        active_window=window_info.get("title", ""),
                        active_app=window_info.get("app", "unknown"),
                        epoch_value=step,
                        action_description=f"{action_type}:{action_args}",
                        action_type=action_type,
                        action_args=action_args,
                    )

                step += 1

        except Exception as e:
            error = str(e)
            terminated_by = "exception"
            partial = effects_count > 0
            logger.error(
                f"Baseline task {task.id} failed with {effects_count} effects applied: {e}"
            )

        duration_ms = (time.time() - start_time) * 1000
        final_screenshot = self.vm_client.screenshot()

        # Final evaluation
        final_success, final_eval_details = self._safe_eval(task, final_screenshot)

        # Success is determined by evaluator, not agent saying "done"
        if error is None:
            success = final_success
        else:
            success = False

        logger.info(
            f"Baseline task {task.id}: success={success}, terminated_by={terminated_by}, "
            f"steps={step}"
        )

        return RealTaskResult(
            task_id=task.id,
            success=success,
            mode="baseline",
            duration_ms=duration_ms,
            steps_taken=step,
            effects_applied=effects_count,
            effects_compensated=0,
            final_screenshot=final_screenshot,
            error=error,
            action_log=action_log,
            partial_state=partial,
        )

    def compare_results(
        self, atomix: RealTaskResult, baseline: RealTaskResult
    ) -> Dict[str, Any]:
        """Compare Atomix and baseline results."""
        return {
            "task_id": atomix.task_id,
            "atomix_success": atomix.success,
            "baseline_success": baseline.success,
            "atomix_partial": atomix.partial_state,
            "baseline_partial": baseline.partial_state,
            "atomix_steps": atomix.steps_taken,
            "baseline_steps": baseline.steps_taken,
            "atomix_effects": atomix.effects_applied,
            "baseline_effects": baseline.effects_applied,
            "atomix_duration_ms": atomix.duration_ms,
            "baseline_duration_ms": baseline.duration_ms,
        }

    def run_task(
        self, task: RealOSWorldTask, agent: AgentProtocol, max_steps: int = 50
    ) -> Dict[str, Any]:
        """Run a task with both Atomix and baseline, return comparison.

        Note: This requires the VM to be reset between runs for fair comparison.
        """
        logger.info(f"Running task: {task.id} - {task.domain}")

        atomix_result = self.run_task_with_agent(task, agent, max_steps)

        if self._desktop_env:
            self._reset_task(task)
        else:
            logger.warning("VM should be reset between Atomix and baseline runs")

        baseline_agent = agent
        reset_fn = getattr(agent, "reset", None)
        if callable(reset_fn):
            reset_fn()
        else:
            baseline_agent = copy.deepcopy(agent)

        baseline_result = self.run_task_baseline(task, baseline_agent, max_steps)

        comparison = self.compare_results(atomix_result, baseline_result)

        logger.info(
            f"Task {task.id}: atomix={atomix_result.success}, baseline={baseline_result.success}"
        )

        return {
            "atomix": atomix_result,
            "baseline": baseline_result,
            "comparison": comparison,
        }

    def close(self) -> None:
        """Clean up resources."""
        self.vm_client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
