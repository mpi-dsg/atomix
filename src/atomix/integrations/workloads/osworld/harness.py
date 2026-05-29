"""
OSWorld workload harness for running and evaluating tasks.

Provides infrastructure for:
- Task definition and loading
- Running tasks with Atomix vs baseline
- Result collection and comparison
- Fault injection during execution
"""

from __future__ import annotations

import logging
import shlex
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.injector import FaultInjector, FaultProfile
from atomix.runtime import AtomixRuntime
from atomix.tool_result import ToolMeta, normalize_tool_result

from .adapters import (
    AppendFileAdapter,
    ReadFileAdapter,
    RunCommandAdapter,
    WriteFileAdapter,
    append_file,
    plan_append_file,
    plan_run_command,
    plan_write_file,
    read_file,
    run_command,
    write_file,
)

logger = logging.getLogger(__name__)


@dataclass
class Task:
    """Definition of an OSWorld task."""

    id: str
    name: str
    description: str
    steps: List[Dict[str, Any]]  # List of tool calls
    setup: Optional[Callable[[Path], None]] = None  # Setup function
    verify: Optional[Callable[[Path], bool]] = None  # Verification function
    expected_files: Dict[str, str] = field(default_factory=dict)  # path -> content


@dataclass
class TaskResult:
    """Result of executing a task."""

    task_id: str
    success: bool
    mode: str  # "atomix" or "baseline"
    duration_ms: float
    effects_applied: int
    effects_compensated: int
    error: Optional[str] = None
    final_state: Dict[str, str] = field(default_factory=dict)  # path -> content
    partial_state: bool = False  # True if state is inconsistent


class OSWorldHarness:
    """Harness for running OSWorld tasks with Atomix or baseline."""

    def __init__(
        self,
        work_dir: Optional[Path] = None,
        fault_profile: Optional[FaultProfile] = None,
    ) -> None:
        self.work_dir = work_dir or Path(tempfile.mkdtemp(prefix="osworld_"))
        self.fault_profile = fault_profile
        self._applied_effects: List[Effect] = []
        self._compensated_count = 0
        self._command_adapter = RunCommandAdapter()

    @staticmethod
    def _resolve_task_path(task_dir: Path, value: str) -> str:
        """Resolve a task-local path and reject escapes from the task sandbox."""
        raw = Path(value)
        if raw.is_absolute():
            raise ValueError(f"Task path must be relative: {value}")
        root = task_dir.resolve()
        resolved = (root / raw).resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Task path escapes task directory: {value}") from exc
        return str(resolved)

    def _resolve_step_args(
        self, task_dir: Path, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Resolve path-bearing tool args into the task sandbox."""
        resolved = dict(args)
        if "path" in resolved:
            resolved["path"] = self._resolve_task_path(task_dir, resolved["path"])
        if "targets" in resolved:
            targets = resolved["targets"]
            if isinstance(targets, str):
                targets = [targets]
            resolved["targets"] = [
                self._resolve_task_path(task_dir, target)
                for target in targets
            ]
        if "command" in resolved:
            resolved["command"] = self._resolve_task_command(
                task_dir, resolved["command"]
            )
        return resolved

    def _resolve_task_command(self, task_dir: Path, command: str) -> str:
        """Resolve path arguments for whitelisted task commands."""
        parts = shlex.split(command)
        if not parts:
            return command
        path_positions = self._command_path_positions(parts)
        if not path_positions:
            return command
        rewritten = list(parts)
        for position in path_positions:
            rewritten[position] = self._resolve_task_path(task_dir, parts[position])
        return shlex.join(rewritten)

    @staticmethod
    def _command_path_positions(parts: list[str]) -> list[int]:
        base_cmd = parts[0]
        args = [(idx, part) for idx, part in enumerate(parts[1:], start=1) if not part.startswith("-")]
        if base_cmd in {"mkdir", "touch"}:
            return [idx for idx, _part in args]
        if base_cmd in {"cp", "mv", "ln"} and len(args) == 2:
            return [idx for idx, _part in args]
        if base_cmd == "chmod" and len(args) >= 2:
            return [args[-1][0]]
        return []

    def _apply_effect(self, effect: Effect) -> None:
        """Effect application callback."""
        self._applied_effects.append(effect)
        if effect.compensation:
            original_compensation = effect.compensation

            def compensation() -> None:
                original_compensation()
                self._compensated_count += 1

            effect.compensation = compensation
        payload = effect.payload

        if "content" in payload and "path" in payload and "before" in payload:
            # Write effect
            p = Path(payload["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(payload["content"], encoding="utf-8")
        elif "appended" in payload and "path" in payload:
            # Append effect
            p = Path(payload["path"])
            with p.open("a", encoding="utf-8") as f:
                f.write(payload["appended"])
        elif "command" in payload:
            result = run_command(
                command=payload["command"],
                targets=payload.get("targets", []),
                adapter=self._command_adapter,
            )
            if result.get("returncode") != 0:
                raise RuntimeError(
                    f"command failed: {result.get('returncode')} - {result.get('stderr')}"
                )

    def _setup_runtime(self) -> AtomixRuntime:
        """Create and configure an AtomixRuntime."""
        runtime = AtomixRuntime(
            apply_effect=self._apply_effect,
            effect_log_path=str(self.work_dir / "effects.jsonl"),
            fault_profile=self.fault_profile,
        )

        # Register adapters
        runtime.register_adapter("read_file", ReadFileAdapter())
        runtime.register_adapter("write_file", WriteFileAdapter())
        runtime.register_adapter("append_file", AppendFileAdapter())
        runtime.register_adapter("run_command", self._command_adapter)

        return runtime

    def run_atomix(self, task: Task) -> TaskResult:
        """Run a task using Atomix transactional semantics."""
        self._applied_effects = []
        self._compensated_count = 0

        task_dir = self.work_dir / f"atomix_{task.id}"
        task_dir.mkdir(parents=True, exist_ok=True)

        # Run setup if provided
        if task.setup:
            task.setup(task_dir)

        runtime = self._setup_runtime()
        start_time = time.time()
        error: Optional[str] = None

        try:
            for i, step in enumerate(task.steps):
                tool = step["tool"]
                args = self._resolve_step_args(task_dir, step.get("args", {}))

                epoch = runtime.epochs.next(trace_id=task.id)

                # Get the tool function
                tool_fn = self._get_tool_fn(tool, runtime, transactional=True)

                # Run through Atomix
                try:
                    _, tx = runtime.run_tool(tool, tool_fn, args, epoch)
                    # Advance frontier to allow commit
                    scopes = runtime.adapters.get(tool).scopes(args)
                    runtime.advance_frontier(scopes, epoch)
                except Exception as e:
                    logger.warning(f"Step {i} failed: {e}")
                    raise

        except Exception as e:
            error = str(e)
            logger.error(f"Task {task.id} failed: {e}")

        duration_ms = (time.time() - start_time) * 1000

        # Collect final state
        final_state = self._collect_state(task_dir)

        # Verify result
        success = error is None
        if success and task.verify:
            success = task.verify(task_dir)

        partial = error is not None and len(self._applied_effects) > 0

        return TaskResult(
            task_id=task.id,
            success=success,
            mode="atomix",
            duration_ms=duration_ms,
            effects_applied=len(self._applied_effects),
            effects_compensated=self._compensated_count,
            error=error,
            final_state=final_state,
            partial_state=partial,
        )

    def run_baseline(self, task: Task) -> TaskResult:
        """Run a task using baseline (immediate writes, no transactions)."""
        task_dir = self.work_dir / f"baseline_{task.id}"
        task_dir.mkdir(parents=True, exist_ok=True)

        # Run setup if provided
        if task.setup:
            task.setup(task_dir)

        # Use same fault injection as Atomix for fair comparison
        injector = FaultInjector(self.fault_profile)

        start_time = time.time()
        error: Optional[str] = None
        effects_count = 0
        partial = False

        try:
            for i, step in enumerate(task.steps):
                tool = step["tool"]
                args = self._resolve_step_args(task_dir, step.get("args", {}))

                # Execute directly (baseline - no transactions)
                # Wrap in injector for fair fault comparison
                if tool == "read_file":
                    injector.call(lambda: read_file(**args))
                elif tool == "write_file":
                    injector.call(lambda a=args: write_file(**a))
                    effects_count += 1
                elif tool == "append_file":
                    injector.call(lambda a=args: append_file(**a))
                    effects_count += 1
                elif tool == "run_command":
                    adapter = RunCommandAdapter()
                    injector.call(lambda a=args: run_command(adapter=adapter, **a))
                    effects_count += 1

        except Exception as e:
            error = str(e)
            partial = effects_count > 0  # Some effects applied before failure
            logger.error(
                f"Baseline task {task.id} failed at step with {effects_count} effects already applied: {e}"
            )

        duration_ms = (time.time() - start_time) * 1000

        # Collect final state
        final_state = self._collect_state(task_dir)

        # Verify result
        success = error is None
        if success and task.verify:
            success = task.verify(task_dir)

        return TaskResult(
            task_id=task.id,
            success=success,
            mode="baseline",
            duration_ms=duration_ms,
            effects_applied=effects_count,
            effects_compensated=0,  # Baseline has no compensation
            error=error,
            final_state=final_state,
            partial_state=partial,
        )

    def run_baseline_with_retries(self, task: Task, max_retries: int = 2) -> TaskResult:
        """Baseline with simple retry on tool failures (no transactions)."""
        task_dir = self.work_dir / f"baseline_retry_{task.id}"
        task_dir.mkdir(parents=True, exist_ok=True)

        if task.setup:
            task.setup(task_dir)

        injector = FaultInjector(self.fault_profile)
        start_time = time.time()
        error: Optional[str] = None
        effects_count = 0
        partial = False

        try:
            for step in task.steps:
                tool = step["tool"]
                args = self._resolve_step_args(task_dir, step.get("args", {}))

                attempts = 0
                while True:
                    try:
                        if tool == "read_file":
                            injector.call(lambda: read_file(**args))
                        elif tool == "write_file":
                            injector.call(lambda a=args: write_file(**a))
                            effects_count += 1
                        elif tool == "append_file":
                            injector.call(lambda a=args: append_file(**a))
                            effects_count += 1
                        elif tool == "run_command":
                            adapter = RunCommandAdapter()
                            injector.call(
                                lambda a=args: run_command(adapter=adapter, **a)
                            )
                            effects_count += 1
                        break
                    except Exception:
                        attempts += 1
                        if attempts > max_retries:
                            raise
                        continue
        except Exception as e:
            error = str(e)
            partial = effects_count > 0
            logger.error(
                "Baseline retry task %s failed after %d effects: %s",
                task.id,
                effects_count,
                e,
            )

        duration_ms = (time.time() - start_time) * 1000
        final_state = self._collect_state(task_dir)
        success = error is None
        if success and task.verify:
            success = task.verify(task_dir)

        return TaskResult(
            task_id=task.id,
            success=success,
            mode="baseline_retry",
            duration_ms=duration_ms,
            effects_applied=effects_count,
            effects_compensated=0,
            error=error,
            final_state=final_state,
            partial_state=partial,
        )

    def run_baseline_saga(self, task: Task) -> TaskResult:
        """Baseline with compensations (saga-style) and no frontier gating."""
        task_dir = self.work_dir / f"baseline_saga_{task.id}"
        task_dir.mkdir(parents=True, exist_ok=True)

        if task.setup:
            task.setup(task_dir)

        start_time = time.time()
        error: Optional[str] = None
        applied: List[Effect] = []

        runtime = self._setup_runtime()

        try:
            for idx, step in enumerate(task.steps):
                tool = step["tool"]
                args = self._resolve_step_args(task_dir, step.get("args", {}))

                epoch = Epoch(idx, trace_id=task.id)
                adapter = runtime.adapters.get(tool)
                tool_fn = self._get_tool_fn(tool, runtime, transactional=True)
                result = tool_fn(**args)
                meta = ToolMeta(
                    tool_name=tool,
                    trace_id=epoch.trace_id,
                    branch_id=epoch.branch_id,
                    attempt=0,
                )
                tool_result = normalize_tool_result(result, meta=meta)
                effect = adapter.to_effect(args, tool_result, epoch)
                self._apply_effect(effect)
                effect.applied = True
                applied.append(effect)
        except Exception as e:
            error = str(e)
            for effect in reversed(applied):
                if effect.compensation:
                    effect.compensation()
        duration_ms = (time.time() - start_time) * 1000

        final_state = self._collect_state(task_dir)
        success = error is None
        if success and task.verify:
            success = task.verify(task_dir)

        return TaskResult(
            task_id=task.id,
            success=success,
            mode="baseline_saga",
            duration_ms=duration_ms,
            effects_applied=len(applied),
            effects_compensated=len([e for e in applied if e.compensation])
            if error
            else 0,
            error=error,
            final_state=final_state,
            partial_state=error is not None and len(applied) > 0,
        )

    def _get_tool_fn(
        self,
        tool: str,
        runtime: AtomixRuntime,
        transactional: bool,
    ) -> Callable[..., Any]:
        """Get the tool function for a given tool name."""
        if tool == "read_file":
            return read_file
        if tool == "write_file":
            return plan_write_file if transactional else write_file
        if tool == "append_file":
            return plan_append_file if transactional else append_file
        if tool == "run_command":
            adapter = self._command_adapter
            if transactional:
                return lambda **kwargs: plan_run_command(adapter=adapter, **kwargs)
            return lambda **kwargs: run_command(adapter=adapter, **kwargs)
        raise ValueError(f"Unknown tool: {tool}")

    def _collect_state(self, task_dir: Path) -> Dict[str, str]:
        """Collect the final state of files in task directory."""
        state: Dict[str, str] = {}
        if task_dir.exists():
            for p in task_dir.rglob("*"):
                if p.is_file():
                    try:
                        rel = p.relative_to(task_dir)
                        state[str(rel)] = p.read_text(encoding="utf-8")
                    except Exception:
                        pass
        return state

    def compare_results(
        self, atomix: TaskResult, baseline: TaskResult
    ) -> Dict[str, Any]:
        """Compare Atomix and baseline results."""
        return {
            "task_id": atomix.task_id,
            "atomix_success": atomix.success,
            "baseline_success": baseline.success,
            "atomix_partial": atomix.partial_state,
            "baseline_partial": baseline.partial_state,
            "atomix_effects": atomix.effects_applied,
            "baseline_effects": baseline.effects_applied,
            "atomix_compensations": atomix.effects_compensated,
            "atomix_duration_ms": atomix.duration_ms,
            "baseline_duration_ms": baseline.duration_ms,
            "state_match": atomix.final_state == baseline.final_state,
        }

    def run_task(self, task: Task) -> Dict[str, Any]:
        """Run a task with both Atomix and baseline, return comparison."""
        logger.info(f"Running task: {task.id} - {task.name}")

        atomix_result = self.run_atomix(task)
        baseline_result = self.run_baseline(task)

        comparison = self.compare_results(atomix_result, baseline_result)

        logger.info(
            f"Task {task.id} complete: atomix={atomix_result.success}, baseline={baseline_result.success}"
        )

        return {
            "atomix": atomix_result,
            "baseline": baseline_result,
            "comparison": comparison,
        }

    def run_all(self, tasks: Sequence[Task]) -> List[Dict[str, Any]]:
        """Run all tasks and collect results."""
        results = []
        for task in tasks:
            result = self.run_task(task)
            results.append(result)
        return results
