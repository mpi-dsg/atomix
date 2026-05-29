#!/usr/bin/env python3
"""
Claude Code PostToolUse hook that commits or aborts an Atomix transaction.

Maps:
- session_id -> trace_id
- tool_use_id -> branch_id
Commits if tool_response.success is True or missing; aborts otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from atomix.adapters.filesystem import FileWriteAdapter
from atomix.effects import Effect
from atomix.integrations.claude_code import complete_tool_call
from atomix.runtime import AtomixRuntime
from atomix.store import SqliteStore

LOG_PATH = Path("logs/claude_post_tool_use.log")
STORE_PATH = Path("logs/atomix.sqlite")

def apply_effect(effect: Effect) -> None:
    payload = effect.payload
    path = payload.get("path")
    content = payload.get("content")
    if path and content is not None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


store = SqliteStore(STORE_PATH)
runtime = AtomixRuntime(apply_effect=apply_effect, store=store)
# Register sample adapters; replace with your adapters.
runtime.register_adapter("file_write", FileWriteAdapter())


def log_line(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def commit_or_abort(
    trace_id: str,
    branch_id: str,
    tool_name: str,
    tool_input: Dict[str, Any],
    tool_response: Dict[str, Any],
) -> bool:
    payload = {
        "session_id": trace_id,
        "tool_use_id": branch_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_response": tool_response,
    }
    outcome = complete_tool_call(payload, runtime=runtime, store=store)
    if outcome.status == "committed":
        log_line(
            f"COMMIT tx={outcome.tx_id} trace={trace_id} branch={branch_id} tool={tool_name}"
        )
        return True
    log_line(
        f"ABORT tx={outcome.tx_id} trace={trace_id} branch={branch_id} tool={tool_name}"
    )
    return False


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    payload = json.loads(raw)
    trace_id = payload.get("session_id") or uuid4().hex
    branch_id = payload.get("tool_use_id") or uuid4().hex
    tool_name = payload.get("tool_name", "unknown")
    tool_input = payload.get("tool_input", {}) or {}
    tool_response = payload.get("tool_response", {})
    committed = commit_or_abort(trace_id, branch_id, tool_name, tool_input, tool_response)

    if not committed:
        out = {
            "decision": "block",
            "reason": f"Atomix aborted branch {branch_id}",
        }
        sys.stdout.write(json.dumps(out))
        sys.exit(0)

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": f"Atomix committed tool {tool_name} for branch {branch_id}",
        }
    }
    sys.stdout.write(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
