"""
Atomix CLI entry point.

Usage:
    python -m atomix [command] [options]

Commands:
    demo        Run the basic speculation/rollback demo
    osworld     Run OSWorld workload tasks
    version     Show version information
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .logging import setup_logging


def cmd_demo(args: argparse.Namespace) -> int:
    """Run the basic Atomix demo."""
    from dataclasses import dataclass, field
    from typing import Any, Dict, List

    from .adapters import ToolAdapter
    from .effects import Effect
    from .epoch import Epoch
    from .runtime import AtomixRuntime
    from .tool_result import ToolMeta, normalize_tool_result

    logger = logging.getLogger("atomix.demo")

    @dataclass
    class InMemoryStore:
        data: Dict[str, Any] = field(default_factory=dict)
        journal: List[str] = field(default_factory=list)

        def plan_write(self, key: str, value: Any) -> Dict[str, Any]:
            before = self.data.get(key)
            return {"key": key, "value": value, "before": before}

        def write_immediate(self, key: str, value: Any) -> None:
            before = self.data.get(key)
            self.data[key] = value
            self.journal.append(f"immediate write {key}: {before} -> {value}")

        def apply_effect(self, effect: Effect) -> None:
            key = next(iter(effect.scopes))
            value = effect.payload["value"]
            before = self.data.get(key)
            self.data[key] = value
            self.journal.append(f"commit {effect.description} (before={before}, after={value})")

        def restore(self, key: str, before: Any) -> None:
            if before is None:
                self.data.pop(key, None)
                self.journal.append(f"compensate delete {key}")
            else:
                self.data[key] = before
                self.journal.append(f"compensate restore {key} -> {before}")

        def snapshot(self) -> Dict[str, Any]:
            return dict(self.data)

    class WriteAdapter(ToolAdapter):
        name = "write"

        def __init__(self, store: InMemoryStore) -> None:
            self.store = store

        def scopes(self, args: dict[str, Any]) -> set[str]:
            return {args["key"]}

        def to_effect(self, args: dict[str, Any], result: Any, epoch: Epoch) -> Effect:
            key = args["key"]
            payload = result.output if hasattr(result, "output") else result
            value = payload["value"]
            before = payload["before"]

            def compensation() -> None:
                self.store.restore(key, before)

            return Effect(
                description=f"write:{key}->{value}@{epoch.value}",
                scopes={key},
                payload={"key": key, "value": value},
                idempotency_key=f"{key}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
                compensation=compensation,
            )

    # Speculation demo
    logger.info("=== Speculation Demo ===")
    logger.info("Baseline: both branches write immediately (contamination)")

    baseline_store = InMemoryStore()
    baseline_store.write_immediate("record", "branch_a")
    baseline_store.write_immediate("record", "branch_b")
    logger.info("Baseline result: %s", baseline_store.snapshot())

    logger.info("Atomix: buffers effects, aborts losing branch")
    atomix_store = InMemoryStore()
    runtime = AtomixRuntime(apply_effect=atomix_store.apply_effect)
    adapter = WriteAdapter(atomix_store)
    runtime.register_adapter("write", adapter)

    base_epoch = Epoch(0, trace_id="spec_demo")
    _, tx_a = runtime.run_tool(
        "write", atomix_store.plan_write, {"key": "record", "value": "branch_a"}, base_epoch.for_branch("A")
    )
    _, tx_b = runtime.run_tool(
        "write", atomix_store.plan_write, {"key": "record", "value": "branch_b"}, base_epoch.for_branch("B")
    )
    runtime.tx_manager.abort(tx_b, "losing branch")
    runtime.advance_frontier({"record"}, base_epoch.for_branch("A"))
    logger.info("Atomix result: %s (only winning branch committed)", atomix_store.snapshot())

    # Partial failure demo
    logger.info("")
    logger.info("=== Partial Failure Demo ===")
    logger.info("Baseline: first step persists even when second fails")

    baseline_pf = InMemoryStore()
    try:
        baseline_pf.write_immediate("order", "step1-applied")
        raise RuntimeError("failure after first step")
    except RuntimeError:
        pass
    logger.info("Baseline result: %s (partial state leaked)", baseline_pf.snapshot())

    logger.info("Atomix: transaction aborted, no state change")
    atomix_pf = InMemoryStore()
    runtime_pf = AtomixRuntime(apply_effect=atomix_pf.apply_effect)
    runtime_pf.register_adapter("write", WriteAdapter(atomix_pf))

    epoch = Epoch(0, trace_id="partial_demo")
    tx = runtime_pf.tx_manager.begin({"order"}, epoch)
    try:
        planned = atomix_pf.plan_write("order", "step1-buffered")
        meta = ToolMeta(
            tool_name="write",
            trace_id=epoch.trace_id,
            branch_id=epoch.branch_id,
            attempt=0,
        )
        tool_result = normalize_tool_result(planned, meta=meta)
        effect = WriteAdapter(atomix_pf).to_effect(
            {"key": "order", "value": "step1-buffered"}, tool_result, epoch
        )
        runtime_pf.tx_manager.record_effect(tx, effect)
        raise RuntimeError("failure after first step")
    except RuntimeError:
        runtime_pf.tx_manager.abort(tx, "failure")
    logger.info("Atomix result: %s (clean state)", atomix_pf.snapshot())

    return 0


def cmd_osworld(args: argparse.Namespace) -> int:
    """Run OSWorld workload tasks."""
    import tempfile

    from .injector import FaultProfile
    from .integrations.workloads.osworld import OSWORLD_TASKS, OSWorldHarness

    logger = logging.getLogger("atomix.osworld")

    # Setup fault injection if requested
    fault_profile = None
    if args.fault_rate > 0:
        fault_profile = FaultProfile(exception_probability=args.fault_rate)
        logger.info("Fault injection enabled: %.0f%% exception rate", args.fault_rate * 100)

    with tempfile.TemporaryDirectory() as tmpdir:
        harness = OSWorldHarness(work_dir=Path(tmpdir), fault_profile=fault_profile)

        if args.task:
            # Run specific task
            task = next((t for t in OSWORLD_TASKS if t.id == args.task), None)
            if not task:
                logger.error("Task not found: %s", args.task)
                logger.info("Available tasks: %s", ", ".join(t.id for t in OSWORLD_TASKS))
                return 1
            tasks = [task]
        else:
            # Run all tasks (or limit)
            limit = args.limit or len(OSWORLD_TASKS)
            tasks = OSWORLD_TASKS[:limit]

        logger.info("Running %d OSWorld task(s)...", len(tasks))

        atomix_pass = 0
        baseline_pass = 0
        baseline_partial = 0

        for task in tasks:
            logger.info("Task: %s - %s", task.id, task.name)
            result = harness.run_task(task)
            comparison = result["comparison"]

            atomix_status = "PASS" if comparison["atomix_success"] else "FAIL"
            baseline_status = "PASS" if comparison["baseline_success"] else "FAIL"
            if comparison["baseline_partial"]:
                baseline_status = "PARTIAL"
                baseline_partial += 1

            logger.info(
                "  Atomix: %s | Baseline: %s | Match: %s",
                atomix_status,
                baseline_status,
                "YES" if comparison["state_match"] else "NO",
            )

            if comparison["atomix_success"]:
                atomix_pass += 1
            if comparison["baseline_success"]:
                baseline_pass += 1

        # Summary
        if args.fault_rate > 0:
            logger.info("")
            logger.info("=== Summary ===")
            logger.info("Atomix:   %d/%d passed", atomix_pass, len(tasks))
            logger.info("Baseline: %d/%d passed (%d partial state leaks)", baseline_pass, len(tasks), baseline_partial)

    return 0


def cmd_version(args: argparse.Namespace) -> int:
    """Show version information."""
    logger = logging.getLogger("atomix")
    logger.info("Atomix version %s", __version__)
    logger.info("Transactional tool semantics with frontier-aligned commits")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="atomix",
        description="Atomix: transactional tool semantics for LLM agents",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # demo command
    demo_parser = subparsers.add_parser("demo", help="Run the basic speculation/rollback demo")
    demo_parser.set_defaults(func=cmd_demo)

    # osworld command
    osworld_parser = subparsers.add_parser("osworld", help="Run OSWorld workload tasks")
    osworld_parser.add_argument(
        "-t", "--task",
        help="Run a specific task by ID (e.g., osw-001)",
    )
    osworld_parser.add_argument(
        "-n", "--limit",
        type=int,
        help="Limit number of tasks to run",
    )
    osworld_parser.add_argument(
        "-f", "--fault-rate",
        type=float,
        default=0.0,
        help="Probability of fault injection per tool call (0.0-1.0)",
    )
    osworld_parser.set_defaults(func=cmd_osworld)

    # version command
    version_parser = subparsers.add_parser("version", help="Show version information")
    version_parser.set_defaults(func=cmd_version)

    args = parser.parse_args(argv)

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    setup_logging(level=level)

    if args.command is None:
        parser.print_help()
        return 2

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
