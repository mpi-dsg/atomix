"""
Filesystem demo showing Atomix transactional semantics on real files.

Creates a temporary directory, simulates speculative branches writing different
contents to the same file, and a partial-failure scenario. Baseline writes
immediately and contaminates state; Atomix buffers effects and commits only
winning branches or compensates on failure.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Dict, Tuple

from atomix.adapters.filesystem import FileWriteAdapter
from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.logging import setup_logging
from atomix.runtime import AtomixRuntime
from atomix.tool_result import ToolMeta, normalize_tool_result

logger = logging.getLogger("atomix.filesystem_demo")


def plan_write(path: Path, content: str) -> Dict[str, str | None]:
    path = path.resolve()
    before = path.read_text(encoding="utf-8") if path.exists() else None
    return {"before": before, "content": content}


def apply_effect(effect: Effect) -> None:
    p = Path(effect.payload["path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(effect.payload["content"], encoding="utf-8")


def speculative_baseline(tmp: Path) -> Tuple[str, str]:
    path = tmp / "record.txt"
    path.write_text("branch_a", encoding="utf-8")
    path.write_text("branch_b", encoding="utf-8")  # losing branch contaminates
    return path.read_text(encoding="utf-8"), "baseline wrote branch_a then branch_b"


def speculative_atomix(tmp: Path) -> Tuple[str, str]:
    runtime = AtomixRuntime(apply_effect=apply_effect)
    adapter = FileWriteAdapter()
    runtime.register_adapter("file_write", adapter)
    trace_id = "fs_demo"
    base_epoch = Epoch(0, trace_id=trace_id)
    target = (tmp / "record.txt").resolve()
    # branch A plan
    plan_a = plan_write(target, "branch_a")
    runtime.run_tool(
        "file_write",
        lambda path, content: {"before": plan_a["before"], "content": content},
        {"path": str(target), "content": "branch_a"},
        base_epoch.for_branch("A"),
    )
    # branch B plan
    plan_b = plan_write(target, "branch_b")
    _, tx_b = runtime.run_tool(
        "file_write",
        lambda path, content: {"before": plan_b["before"], "content": content},
        {"path": str(target), "content": "branch_b"},
        base_epoch.for_branch("B"),
    )
    runtime.tx_manager.abort(tx_b, "losing branch")
    runtime.advance_frontier({str(target)}, base_epoch)
    return target.read_text(encoding="utf-8"), "atomix committed branch_a, aborted branch_b"


def partial_failure_baseline(tmp: Path) -> Tuple[str, str]:
    path = (tmp / "order.txt").resolve()
    path.write_text("step1", encoding="utf-8")
    try:
        raise RuntimeError("failure after step1")
    except Exception as exc:  # noqa: BLE001
        return path.read_text(encoding="utf-8"), f"baseline left partial state ({exc})"


def partial_failure_atomix(tmp: Path) -> Tuple[str, str]:
    runtime = AtomixRuntime(apply_effect=apply_effect)
    adapter = FileWriteAdapter()
    runtime.register_adapter("file_write", adapter)
    epoch = Epoch(0, trace_id="pf_demo")
    target = (tmp / "order.txt").resolve()
    tx = runtime.tx_manager.begin({str(target)}, epoch)
    try:
        plan = plan_write(target, "step1")
        meta = ToolMeta(
            tool_name="file_write",
            trace_id=epoch.trace_id,
            branch_id=epoch.branch_id,
            attempt=0,
        )
        tool_result = normalize_tool_result(
            {"before": plan["before"], "content": "step1"},
            meta=meta,
        )
        effect = adapter.to_effect(
            {"path": str(target), "content": "step1"},
            tool_result,
            epoch,
        )
        runtime.tx_manager.record_effect(tx, effect)
        raise RuntimeError("failure after step1")
    except Exception as exc:  # noqa: BLE001
        runtime.tx_manager.abort(tx, str(exc))
    if target.exists():
        return target.read_text(encoding="utf-8"), "atomix failed to compensate"
    return "", "atomix compensated and removed file"


def main() -> None:
    setup_logging(level=logging.INFO)

    with tempfile.TemporaryDirectory() as tmpdir_base, tempfile.TemporaryDirectory() as tmpdir_atomix:
        base_value, base_msg = speculative_baseline(Path(tmpdir_base))
        atomix_value, atomix_msg = speculative_atomix(Path(tmpdir_atomix))
        logger.info("Speculative baseline: %s - %s", base_value, base_msg)
        logger.info("Speculative Atomix: %s - %s", atomix_value, atomix_msg)

    with tempfile.TemporaryDirectory() as tmpdir_base, tempfile.TemporaryDirectory() as tmpdir_atomix:
        base_value, base_msg = partial_failure_baseline(Path(tmpdir_base))
        atomix_value, atomix_msg = partial_failure_atomix(Path(tmpdir_atomix))
        logger.info("Partial baseline: %s - %s", base_value, base_msg)
        logger.info("Partial Atomix: %s - %s", atomix_value, atomix_msg)


if __name__ == "__main__":
    main()
