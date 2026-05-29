#!/usr/bin/env python3
"""
Claude Code PreToolUse hook that opens an Atomix transaction per tool call.

Maps:
- session_id -> trace_id
- tool_use_id -> branch_id
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from atomix.adapters.filesystem import FileWriteAdapter  # example adapter
from atomix.integrations.claude_code import begin_tool_call
from atomix.runtime import AtomixRuntime
from atomix.store import SqliteStore

LOG_PATH = Path("logs/claude_pre_tool_use.log")
STORE_PATH = Path("logs/atomix.sqlite")

store = SqliteStore(STORE_PATH)
runtime = AtomixRuntime(apply_effect=lambda eff: None, store=store)
# Register sample adapters as needed; users should adjust to their tool set.
runtime.register_adapter("file_write", FileWriteAdapter())


def log_line(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def begin_transaction(
    trace_id: str, branch_id: str, tool_name: str, tool_input: Dict[str, Any]
) -> str:
    payload = {
        "session_id": trace_id,
        "tool_use_id": branch_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
    }
    result = begin_tool_call(payload, runtime=runtime, store=store)
    log_line(
        f"BEGIN tx={result['tx_id']} trace={trace_id} branch={branch_id} tool={tool_name}"
    )
    return result["tx_id"]


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    payload = json.loads(raw)
    session_id = payload.get("session_id") or uuid4().hex
    branch_id = payload.get("tool_use_id") or uuid4().hex
    tool_name = payload.get("tool_name", "unknown")
    tool_input = payload.get("tool_input", {})

    tx_id = begin_transaction(session_id, branch_id, tool_name, tool_input)

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": f"Atomix tx {tx_id} opened",
        }
    }
    sys.stdout.write(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
