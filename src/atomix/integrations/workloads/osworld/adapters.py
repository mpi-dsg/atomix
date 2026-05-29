"""
OSWorld tool adapters with compensation support.

Adapters for:
- read_file: Read file contents (read-only, no compensation)
- write_file: Write/overwrite file contents (compensation: restore previous)
- append_file: Append to file (compensation: truncate to previous size)
- run_command: Execute whitelisted commands (limited compensation)
"""

from __future__ import annotations

import shlex
import stat
import subprocess
from pathlib import Path
from typing import Any, Set

from atomix.adapters import ToolAdapter
from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.tool_result import ToolResult


class ReadFileAdapter(ToolAdapter):
    """Adapter for reading file contents. Read-only, no compensation needed."""

    name = "read_file"

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        # Read-only operations don't need scope locking
        return set()

    def to_effect(self, args: dict[str, Any], result: ToolResult, epoch: Epoch) -> Effect:
        path = Path(args["path"]).resolve()
        return Effect(
            description=f"read:{path}@{epoch.value}",
            scopes=set(),  # Read-only
            payload={"path": str(path), "content": result.output},
            idempotency_key=f"read:{path}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
            compensation=None,  # No compensation for reads
        )


class WriteFileAdapter(ToolAdapter):
    """Adapter for writing/overwriting file contents with compensation."""

    name = "write_file"

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        return {str(Path(args["path"]).resolve())}

    def to_effect(self, args: dict[str, Any], result: ToolResult, epoch: Epoch) -> Effect:
        path = Path(args["path"]).resolve()
        payload = result.output if isinstance(result.output, dict) else {}
        before = payload.get("before")
        content = payload.get("content", result.output)

        def compensation() -> None:
            if before is None:
                # File didn't exist before, delete it
                if path.exists():
                    path.unlink()
            else:
                # Restore previous content
                path.write_text(before, encoding="utf-8")

        return Effect(
            description=f"write:{path}@{epoch.value}",
            scopes={str(path)},
            payload={"path": str(path), "content": content, "before": before},
            idempotency_key=f"write:{path}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
            compensation=compensation,
        )


class AppendFileAdapter(ToolAdapter):
    """Adapter for appending to files with compensation (truncate)."""

    name = "append_file"

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        return {str(Path(args["path"]).resolve())}

    def to_effect(self, args: dict[str, Any], result: ToolResult, epoch: Epoch) -> Effect:
        path = Path(args["path"]).resolve()
        payload = result.output if isinstance(result.output, dict) else {}
        previous_size = payload.get("previous_size", 0)
        appended = payload.get("appended")

        def compensation() -> None:
            if path.exists():
                # Truncate to previous size
                content = path.read_text(encoding="utf-8")
                path.write_text(content[:previous_size], encoding="utf-8")

        return Effect(
            description=f"append:{path}@{epoch.value}",
            scopes={str(path)},
            payload={
                "path": str(path),
                "appended": appended,
                "previous_size": previous_size,
            },
            idempotency_key=f"append:{path}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
            compensation=compensation,
        )


class RunCommandAdapter(ToolAdapter):
    """Adapter for running whitelisted shell commands.

    Only safe, reversible commands are allowed. Each command type has
    its own compensation strategy.
    """

    name = "run_command"

    # Whitelist of allowed commands with their compensation strategies
    ALLOWED_COMMANDS = {
        "mkdir": "rmdir",      # Create directory -> remove directory
        "touch": "rm",         # Create empty file -> remove file
        "cp": "rm",            # Copy file -> remove copy
        "mv": "mv_reverse",    # Move file -> move back
        "chmod": "chmod",      # Change mode -> restore mode
        "ln": "rm",            # Create link -> remove link
    }

    def __init__(self, extra_allowed: dict[str, str] | None = None) -> None:
        """Initialize with optional extra allowed commands."""
        self.allowed = dict(self.ALLOWED_COMMANDS)
        if extra_allowed:
            self.allowed.update(extra_allowed)

    def scopes(self, args: dict[str, Any]) -> Set[str]:
        # Scope is the target path(s) affected by the command
        command = args.get("command", "")
        targets = _resolve_targets(command, args.get("targets"))
        return {str(Path(t).resolve()) for t in targets}

    def to_effect(self, args: dict[str, Any], result: ToolResult, epoch: Epoch) -> Effect:
        command = args.get("command", "")
        targets = _resolve_targets(command, args.get("targets"))
        payload = result.output if isinstance(result.output, dict) else {}
        command_parts = payload.get("command_parts")
        if not command_parts:
            command_parts = _split_command(command)

        base_cmd = command_parts[0] if command_parts else ""
        scopes = {str(Path(t).resolve()) for t in targets}

        def compensation() -> None:
            # Compensation depends on command type
            comp_strategy = self.allowed.get(base_cmd)
            if not comp_strategy or not targets:
                return

            if comp_strategy == "rmdir":
                for target in targets:
                    p = Path(target)
                    if p.exists() and p.is_dir():
                        try:
                            p.rmdir()
                        except OSError:
                            pass  # Directory not empty
            elif comp_strategy == "rm":
                rm_targets = targets
                if base_cmd in {"cp", "ln"} and len(targets) == 2:
                    rm_targets = [targets[1]]
                for target in rm_targets:
                    p = Path(target)
                    if p.exists() and not p.is_dir():
                        p.unlink()
            elif comp_strategy == "mv_reverse":
                # For mv, result contains original source/dest
                src = payload.get("original_src")
                dst = payload.get("original_dst")
                if src and dst and Path(dst).exists():
                    Path(dst).rename(src)
            elif comp_strategy == "chmod":
                target = payload.get("target")
                original_mode = payload.get("original_mode")
                if target and original_mode is not None:
                    try:
                        Path(target).chmod(original_mode)
                    except OSError:
                        pass

        return Effect(
            description=f"cmd:{base_cmd}:{','.join(targets)}@{epoch.value}",
            scopes=scopes,
            payload={
                "command": command,
                "command_parts": command_parts,
                "targets": targets,
                "stdout": payload.get("stdout", ""),
                "stderr": payload.get("stderr", ""),
                "returncode": payload.get("returncode", 0),
                "original_src": payload.get("original_src"),
                "original_dst": payload.get("original_dst"),
                "original_mode": payload.get("original_mode"),
                "target": payload.get("target"),
            },
            idempotency_key=f"cmd:{command}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
            compensation=compensation,
        )

    def is_allowed(self, command: str) -> bool:
        """Check if command is in whitelist."""
        try:
            parts = _split_command(command)
        except (ValueError, PermissionError):
            return False
        base_cmd = parts[0] if parts else ""
        return base_cmd in self.allowed


# Tool functions to be used with the adapters

def read_file(path: str) -> str:
    """Read file contents."""
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return p.read_text(encoding="utf-8")


def write_file(path: str, content: str) -> dict[str, Any]:
    """Write content to file, returning before/after for compensation."""
    p = Path(path).resolve()
    before = p.read_text(encoding="utf-8") if p.exists() else None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"before": before, "content": content}


def append_file(path: str, content: str) -> dict[str, Any]:
    """Append content to file, returning size info for compensation."""
    p = Path(path).resolve()
    previous_size = len(p.read_text(encoding="utf-8")) if p.exists() else 0
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(content)
    return {"previous_size": previous_size, "appended": content}


def _split_command(command: str) -> list[str]:
    if not command:
        raise ValueError("Command is required")
    if any(token in command for token in (";", "|", "&")):
        raise PermissionError("Shell operators are not allowed")
    parts = shlex.split(command)
    if not parts:
        raise ValueError("Command is required")
    return parts


def _normalize_targets(targets: list[str] | str | None) -> list[str]:
    if targets is None:
        return []
    if isinstance(targets, str):
        return [targets]
    return list(targets)


def _derive_targets(base_cmd: str, parts: list[str]) -> list[str]:
    args = [p for p in parts[1:] if not p.startswith("-")]
    if base_cmd in {"mkdir", "touch"}:
        return args
    if base_cmd in {"cp", "mv", "ln"}:
        if len(args) != 2:
            raise ValueError(f"{base_cmd} requires exactly 2 targets")
        return args
    if base_cmd == "chmod":
        if len(args) < 2:
            raise ValueError("chmod requires a mode and a target")
        return [args[-1]]
    return []


def _resolve_targets(command: str, targets: list[str] | str | None) -> list[str]:
    if not command:
        return _normalize_targets(targets)
    parts = _split_command(command)
    base_cmd = parts[0]
    derived = _derive_targets(base_cmd, parts)
    explicit = _normalize_targets(targets)
    if explicit and derived:
        resolved_explicit = {str(Path(t).resolve()) for t in explicit}
        resolved_derived = {str(Path(t).resolve()) for t in derived}
        if resolved_explicit != resolved_derived:
            raise ValueError("Targets do not match command arguments")
        return explicit
    if derived:
        return derived
    return explicit


def run_command(
    command: str, targets: list[str] | str | None, adapter: RunCommandAdapter
) -> dict[str, Any]:
    """Run a whitelisted command."""
    parts = _split_command(command)
    base_cmd = parts[0]
    if base_cmd not in adapter.allowed:
        raise PermissionError(f"Command not allowed: {command}")

    resolved_targets = _resolve_targets(command, targets)

    original_src = None
    original_dst = None
    original_mode = None
    chmod_target = None

    if base_cmd == "mv":
        original_src, original_dst = _derive_targets(base_cmd, parts)

    if base_cmd == "chmod":
        chmod_target = _derive_targets(base_cmd, parts)[0]
        try:
            original_mode = stat.S_IMODE(Path(chmod_target).stat().st_mode)
        except FileNotFoundError:
            original_mode = None

    result = subprocess.run(
        parts,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
        "command_parts": parts,
        "targets": resolved_targets,
        "original_src": original_src,
        "original_dst": original_dst,
        "original_mode": original_mode,
        "target": chmod_target,
    }


def plan_write_file(path: str, content: str) -> dict[str, Any]:
    """Plan a file write without applying it."""
    p = Path(path).resolve()
    before = p.read_text(encoding="utf-8") if p.exists() else None
    return {"before": before, "content": content}


def plan_append_file(path: str, content: str) -> dict[str, Any]:
    """Plan a file append without applying it."""
    p = Path(path).resolve()
    previous_size = len(p.read_text(encoding="utf-8")) if p.exists() else 0
    return {"previous_size": previous_size, "appended": content}


def plan_run_command(
    command: str, targets: list[str] | str | None, adapter: RunCommandAdapter
) -> dict[str, Any]:
    """Plan a whitelisted command without executing it."""
    parts = _split_command(command)
    base_cmd = parts[0]
    if base_cmd not in adapter.allowed:
        raise PermissionError(f"Command not allowed: {command}")

    resolved_targets = _resolve_targets(command, targets)

    original_src = None
    original_dst = None
    original_mode = None
    chmod_target = None

    if base_cmd == "mv":
        original_src, original_dst = _derive_targets(base_cmd, parts)

    if base_cmd == "chmod":
        chmod_target = _derive_targets(base_cmd, parts)[0]
        try:
            original_mode = stat.S_IMODE(Path(chmod_target).stat().st_mode)
        except FileNotFoundError:
            original_mode = None

    return {
        "stdout": "",
        "stderr": "",
        "returncode": 0,
        "command_parts": parts,
        "targets": resolved_targets,
        "original_src": original_src,
        "original_dst": original_dst,
        "original_mode": original_mode,
        "target": chmod_target,
    }
