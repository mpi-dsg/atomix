from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

CACHE_PATH = Path("/tmp/atomix_hook_cache.jsonl")


def record_entry(entry: Dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def find_entry(trace_id: str, branch_id: str, tool_name: str) -> Optional[Dict[str, Any]]:
    if not CACHE_PATH.exists():
        return None
    with CACHE_PATH.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    for line in reversed(lines):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            data.get("trace_id") == trace_id
            and data.get("branch_id") == branch_id
            and data.get("tool_name") == tool_name
        ):
            return data
    return None
