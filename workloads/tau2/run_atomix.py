#!/usr/bin/env python3
"""Atomix-aware tau2 runner wrapper.

Runs tau2 via its Python API for all modes, with Atomix tool wrapping for
Tx-Full/No-Frontier and direct tool calls for No-Tx.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Set


TAU2_SRC = Path("/data/tau2-bench/src")
TAU2_VENV = Path("/data/tau2-bench/.venv/bin/python")


def _ensure_tau2_on_path() -> None:
    if TAU2_SRC.exists() and str(TAU2_SRC) not in sys.path:
        sys.path.insert(0, str(TAU2_SRC))


def _parse_json_arg(value: str | None) -> dict | None:
    if not value:
        return None
    return json.loads(value)


def _tool_scopes(tool_name: str) -> Set[str]:
    return {f"tau2:{tool_name}"}


# Global fault metrics for this run
_fault_metrics = {"faults_injected": 0, "retries_attempted": 0, "retries_succeeded": 0}


def _build_run_config(args: argparse.Namespace):
    _ensure_tau2_on_path()
    from tau2.config import (
        DEFAULT_AGENT_IMPLEMENTATION,
        DEFAULT_LLM_AGENT,
        DEFAULT_LLM_TEMPERATURE_AGENT,
        DEFAULT_LLM_TEMPERATURE_USER,
        DEFAULT_LLM_USER,
        DEFAULT_MAX_CONCURRENCY,
        DEFAULT_MAX_ERRORS,
        DEFAULT_MAX_STEPS,
        DEFAULT_NUM_TRIALS,
        DEFAULT_LOG_LEVEL,
    )
    from tau2.data_model.simulation import RunConfig

    agent = args.agent or DEFAULT_AGENT_IMPLEMENTATION
    llm_agent = args.agent_llm or DEFAULT_LLM_AGENT
    llm_args_agent = args.agent_llm_args or {
        "temperature": DEFAULT_LLM_TEMPERATURE_AGENT
    }
    user = args.user or "user_simulator"
    llm_user = args.user_llm or DEFAULT_LLM_USER
    llm_args_user = args.user_llm_args or {"temperature": DEFAULT_LLM_TEMPERATURE_USER}

    return RunConfig(
        domain=args.domain,
        task_set_name=args.task_set_name,
        task_split_name=args.task_split_name,
        task_ids=args.task_ids,
        num_tasks=args.num_tasks,
        agent=agent,
        llm_agent=llm_agent,
        llm_args_agent=llm_args_agent,
        user=user,
        llm_user=llm_user,
        llm_args_user=llm_args_user,
        num_trials=args.num_trials or DEFAULT_NUM_TRIALS,
        max_steps=args.max_steps or DEFAULT_MAX_STEPS,
        max_errors=args.max_errors or DEFAULT_MAX_ERRORS,
        save_to=args.save_to,
        max_concurrency=args.max_concurrency or DEFAULT_MAX_CONCURRENCY,
        seed=args.seed,
        log_level=args.log_level or DEFAULT_LOG_LEVEL,
        enforce_communication_protocol=args.enforce_communication_protocol,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", default="Tx-Full")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--task-set-name", default=None)
    parser.add_argument("--task-split-name", default=None)
    parser.add_argument("--task-ids", nargs="*", default=None)
    parser.add_argument("--num-tasks", type=int, default=None)
    parser.add_argument("--num-trials", type=int, default=1)
    parser.add_argument("--agent", default=None)
    parser.add_argument("--agent-llm", default=None)
    parser.add_argument("--agent-llm-args", default=None)
    parser.add_argument("--user", default=None)
    parser.add_argument("--user-llm", default=None)
    parser.add_argument("--user-llm-args", default=None)
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--max-errors", type=int, default=10)
    parser.add_argument("--save-to", default=None)
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--log-level", default=None)
    parser.add_argument("--enforce-communication-protocol", action="store_true")
    parser.add_argument("--baseline", action="store_true", help="Alias for No-Tx")
    parser.add_argument("--no-frontier", action="store_true")
    parser.add_argument("--fault-probability", type=float, default=0.0)
    parser.add_argument("--fault-duplicate", type=float, default=0.0)
    parser.add_argument("--fault-delay-max", type=float, default=0.0)
    args = parser.parse_args()

    mode = args.mode
    if args.baseline:
        mode = "No-Tx"
    if args.no_frontier:
        mode = "No-Frontier"

    args.agent_llm_args = _parse_json_arg(args.agent_llm_args)
    args.user_llm_args = _parse_json_arg(args.user_llm_args)

    _ensure_tau2_on_path()
    # Force system + tau2 venv site-packages to resolve deps
    for path in (
        "/usr/lib/python3/dist-packages",
        "/usr/lib/python3.11/dist-packages",
        "/usr/local/lib/python3.11/dist-packages",
        "/data/tau2-bench/.venv/lib/python3.11/site-packages",
    ):
        if path not in sys.path:
            sys.path.append(path)
    # Ensure Atomix src is importable
    atomix_root = Path(__file__).resolve().parents[2]
    atomix_src = atomix_root / "src"
    if atomix_src.exists() and str(atomix_src) not in sys.path:
        sys.path.insert(0, str(atomix_src))

    from tau2.registry import registry
    from tau2.run import get_tasks, run_tasks

    # Patch litellm.completion to record token usage. tau-bench's user
    # simulator and agent both go through litellm; one patch covers both.
    try:
        import litellm  # type: ignore
        from atomix.usage_log import record_usage
        if not getattr(litellm, "_atomix_usage_patched", False):
            _orig_completion = litellm.completion
            _tau_run_id = f"tau2:{args.domain}:{mode}:fp{args.fault_probability}"

            def _patched_completion(*a, **kw):
                resp = _orig_completion(*a, **kw)
                try:
                    usage = resp.get("usage") if isinstance(resp, dict) else getattr(resp, "usage", None)
                    if usage is not None:
                        if isinstance(usage, dict):
                            in_t = usage.get("prompt_tokens", 0)
                            out_t = usage.get("completion_tokens", 0)
                        else:
                            in_t = getattr(usage, "prompt_tokens", 0)
                            out_t = getattr(usage, "completion_tokens", 0)
                        record_usage(
                            provider="openai",
                            model=str(kw.get("model", "")),
                            input_tokens=in_t,
                            output_tokens=out_t,
                            run_id=_tau_run_id,
                        )
                except Exception:
                    pass
                return resp

            litellm.completion = _patched_completion  # type: ignore[assignment]
            # tau-bench imports `from litellm import completion`, capturing
            # a local reference at import time. Update that reference too so
            # the patch actually intercepts the calls.
            for mod_name in (
                "tau2.utils.llm_utils",
                "tau2.agent.llm_agent",
                "tau2.user.user_simulator",
            ):
                mod = sys.modules.get(mod_name)
                if mod is not None and hasattr(mod, "completion"):
                    setattr(mod, "completion", _patched_completion)
            litellm._atomix_usage_patched = True  # type: ignore[attr-defined]
    except Exception:
        pass
    from tau2.environment.environment import Environment

    config = _build_run_config(args)

    task_set_name = config.task_set_name or config.domain
    tasks = get_tasks(
        task_set_name=task_set_name,
        task_split_name=config.task_split_name,
        task_ids=config.task_ids,
        num_tasks=config.num_tasks,
    )

    import random as _random

    from atomix.injector import FaultProfile

    fault_profile = None
    if args.fault_probability > 0 or args.fault_duplicate > 0 or args.fault_delay_max > 0:
        fault_profile = FaultProfile(
            exception_probability=args.fault_probability,
            duplicate_probability=args.fault_duplicate,
            max_delay_s=args.fault_delay_max,
        )

    def _patch_replay_guard(env):
        """Patch set_state() to disable fault injection during replay phase.

        tau-bench replays historical tool calls in set_state() and verifies
        deterministic results.  Fault injection during replay causes mismatches
        and crashes.  We flag replay mode so the fault wrapper can skip.
        """
        env._atomix_replay = True  # start in replay mode
        original_set_state = env.set_state

        def guarded_set_state(*a, **kw):
            env._atomix_replay = True
            try:
                return original_set_state(*a, **kw)
            finally:
                env._atomix_replay = False  # replay done; enable faults

        env.set_state = guarded_set_state  # type: ignore[method-assign]
        return env

    if mode == "No-Tx":
        # No-Tx: inject faults WITHOUT Atomix protection
        if fault_profile and args.fault_probability > 0:
            original_constructor = registry.get_env_constructor(config.domain)

            def notx_wrapped_constructor(*cargs, **ckwargs):
                env = original_constructor(*cargs, **ckwargs)
                _patch_replay_guard(env)
                original_make_tool_call = env.make_tool_call

                def faulty_make_tool_call(
                    tool_name: str, requestor: str = "assistant", **kwargs
                ):
                    if getattr(env, "_atomix_replay", False):
                        return original_make_tool_call(tool_name, requestor=requestor, **kwargs)
                    if _random.random() < args.fault_probability:
                        _fault_metrics["faults_injected"] += 1
                        raise RuntimeError(f"Injected fault: exception (No-Tx, tool={tool_name})")
                    return original_make_tool_call(tool_name, requestor=requestor, **kwargs)

                env.make_tool_call = faulty_make_tool_call  # type: ignore[method-assign]
                return env

            registry._domains[config.domain] = notx_wrapped_constructor

    else:
        from atomix.runtime import AtomixRuntime
        from atomix.adapters import ToolAdapter
        from atomix.epoch import EpochManager
        from atomix.effects import Effect
        from atomix.tool_result import ToolResult

        class Tau2ToolAdapter(ToolAdapter):
            def __init__(self, name: str):
                self.name = name

            def scopes(self, args: Dict[str, Any]) -> Set[str]:
                return _tool_scopes(self.name)

            def to_effect(self, args: Dict[str, Any], result: ToolResult, epoch):
                return Effect(
                    description=f"tau2:{self.name}",
                    scopes=self.scopes(args),
                    payload={"tool": self.name, "args": args, "result": result.output},
                    idempotency_key=f"{epoch.trace_id}:{epoch.value}:{self.name}",
                )

        def wrap_environment(env: Environment) -> Environment:
            _patch_replay_guard(env)
            runtime = AtomixRuntime(
                apply_effect=lambda eff: None,
                effect_log_path=None,
                frontier_enabled=mode not in ("No-Frontier", "CR"),
                fault_profile=fault_profile,
            )
            # New A1 mechanism baselines: swap tx_manager once per env wrap.
            # tau-bench creates a fresh env per task so the lock state in
            # MutexWalRollback resets; OCC versions persist across the env
            # which is fine for E1's clean-success measurement.
            if mode in {"Mutex+WAL+Rollback", "TCC-Confirm", "OCC-Revalidate-and-Retry"}:
                from atomix.baselines import (
                    MutexWalRollback,
                    OCCRevalidateRetry,
                    TCCConfirm,
                )
                _apply = lambda eff: None
                if mode == "Mutex+WAL+Rollback":
                    runtime.tx_manager = MutexWalRollback(_apply)
                elif mode == "TCC-Confirm":
                    runtime.tx_manager = TCCConfirm(_apply)
                else:
                    runtime.tx_manager = OCCRevalidateRetry(_apply, retry_budget=3)
            epoch_manager = EpochManager()
            original_make_tool_call = env.make_tool_call

            def atomix_make_tool_call(
                tool_name: str, requestor: str = "assistant", **kwargs
            ):
                # Skip fault injection during tau-bench replay phase
                if getattr(env, "_atomix_replay", False):
                    return original_make_tool_call(tool_name, requestor=requestor, **kwargs)

                max_retries = 3 if mode in ("Tx-Full", "CR") else 0
                last_err = None
                for attempt in range(max_retries + 1):
                    epoch = epoch_manager.next(trace_id="tau2")
                    adapter = Tau2ToolAdapter(tool_name)
                    runtime.register_adapter(tool_name, adapter)
                    try:
                        result, _ = runtime.run_tool(
                            tool_name,
                            lambda **args: original_make_tool_call(
                                tool_name, requestor=requestor, **args
                            ),
                            kwargs,
                            epoch,
                        )
                        if attempt > 0:
                            _fault_metrics["retries_succeeded"] += 1
                        return result
                    except Exception as e:
                        _fault_metrics["faults_injected"] += 1
                        last_err = e
                        if attempt < max_retries:
                            _fault_metrics["retries_attempted"] += 1
                        if attempt >= max_retries:
                            raise
                raise last_err  # unreachable but satisfies type checker

            env.make_tool_call = atomix_make_tool_call  # type: ignore[method-assign]
            return env

        original_constructor = registry.get_env_constructor(config.domain)

        def wrapped_constructor(*cargs, **ckwargs):
            env = original_constructor(*cargs, **ckwargs)
            return wrap_environment(env)

        registry._domains[config.domain] = wrapped_constructor

    from tau2.evaluator.evaluator import EvaluationType
    from tau2.evaluator import evaluator as _evaluator_mod
    from tau2.data_model.simulation import RewardInfo
    import tau2.run as _tau2_run_mod

    # Patch evaluator to be lenient with fault-injected conversations.
    # Must patch BOTH the module attribute AND tau2.run's local reference,
    # because tau2.run imports evaluate_simulation directly at import time.
    _original_evaluate = _evaluator_mod.evaluate_simulation

    def _lenient_evaluate(simulation, task, evaluation_type, **kwargs):
        try:
            return _original_evaluate(simulation, task, evaluation_type, **kwargs)
        except Exception as e:
            # Fault injection causes set_state() replay mismatches when the
            # evaluator tries to rebuild environment state from a conversation
            # that contains fault-error tool responses.
            import logging as _logging
            _logging.getLogger("atomix.tau2").warning(
                "Evaluation failed (fault replay mismatch): %s", str(e)[:200]
            )
            return RewardInfo(reward=0.0)

    _evaluator_mod.evaluate_simulation = _lenient_evaluate
    _tau2_run_mod.evaluate_simulation = _lenient_evaluate

    run_error = None
    try:
        run_tasks(
            domain=config.domain,
            tasks=tasks,
            agent=config.agent,
            user=config.user,
            llm_agent=config.llm_agent,
            llm_args_agent=config.llm_args_agent,
            llm_user=config.llm_user,
            llm_args_user=config.llm_args_user,
            num_trials=config.num_trials,
            max_steps=config.max_steps,
            max_errors=config.max_errors,
            save_to=config.save_to,
            console_display=False,
            evaluation_type=EvaluationType.ALL,
            max_concurrency=config.max_concurrency,
            seed=config.seed,
            log_level=config.log_level,
            enforce_communication_protocol=config.enforce_communication_protocol,
        )
    except (ValueError, Exception) as e:
        run_error = str(e)[:200]

    # Output metrics summary
    summary = {
        "mode": mode,
        "fault_probability": args.fault_probability,
        "tasks": config.task_ids or f"first-{config.num_tasks or 'all'}",
        "domain": config.domain,
        "fault_metrics": _fault_metrics.copy(),
        "error": run_error,
        "completed": run_error is None,
    }
    # Write metrics alongside results.
    #
    # tau-bench writes its full simulation to `save_to`; the wrapper writes a
    # small metrics summary to a *sibling* path. We derive the sibling by
    # appending `_metrics.json` to the stem so the metrics never overwrite
    # tau-bench's own data, regardless of the save-to extension.
    if config.save_to:
        save_to_path = Path(config.save_to)
        metrics_path = save_to_path.with_name(save_to_path.stem + "_metrics.json")
    else:
        metrics_path = None
    if metrics_path:
        Path(metrics_path).parent.mkdir(parents=True, exist_ok=True)
        Path(metrics_path).write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
