"""Claude Code hook helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from ..epoch import Epoch
from ..transactions import CommitBlocked
from ..effects import Transaction
from ..tool_result import ToolMeta, normalize_tool_result
from ..store import SqliteStore
from ..runtime import AtomixRuntime


@dataclass(frozen=True)
class HookOutcome:
    status: str
    tx_id: str


def begin_tool_call(
    payload: Dict[str, Any],
    *,
    runtime: AtomixRuntime,
    store: SqliteStore,
) -> Dict[str, Any]:
    trace_id = payload["session_id"]
    branch_id = payload["tool_use_id"]
    tool_name = payload["tool_name"]
    tool_input = payload.get("tool_input", {})

    adapter = runtime.adapters.get(tool_name)
    scopes = adapter.scopes(tool_input)
    epoch = runtime.epochs.next(trace_id=trace_id, branch_id=branch_id)
    tx = runtime.tx_manager.begin(scopes, epoch)

    store.save_tool_call(
        trace_id=trace_id,
        branch_id=branch_id,
        tool_name=tool_name,
        tx_id=tx.tx_id,
        epoch=epoch.value,
        scopes=scopes,
        tool_input=tool_input,
    )
    store.save_transaction(
        {
            "tx_id": tx.tx_id,
            "trace_id": trace_id,
            "branch_id": branch_id,
            "epoch": epoch.value,
            "status": tx.status,
            "scopes": list(scopes),
            "reason": None,
        }
    )

    return {
        "tx_id": tx.tx_id,
        "trace_id": trace_id,
        "branch_id": branch_id,
        "tool_name": tool_name,
    }


def complete_tool_call(
    payload: Dict[str, Any],
    *,
    runtime: AtomixRuntime,
    store: SqliteStore,
) -> HookOutcome:
    trace_id = payload["session_id"]
    branch_id = payload["tool_use_id"]
    tool_name = payload["tool_name"]
    tool_input = payload.get("tool_input", {})
    tool_response = payload.get("tool_response", {})

    saved = store.load_tool_call(trace_id, branch_id, tool_name)
    if saved:
        tx_id = saved["tx_id"]
        epoch = Epoch(saved["epoch"], trace_id=trace_id, branch_id=branch_id)
        scopes = set(saved["scopes"])
        if not tool_input:
            tool_input = saved["tool_input"]
    else:
        adapter = runtime.adapters.get(tool_name)
        scopes = adapter.scopes(tool_input)
        epoch = runtime.epochs.next(trace_id=trace_id, branch_id=branch_id)
        tx_id = f"tx:{trace_id}:{branch_id}:{epoch.value}"

    tx = Transaction(
        tx_id=tx_id,
        epoch=epoch,
        scopes=scopes,
        effects=[],
        status="pending",
    )

    meta = ToolMeta(
        tool_name=tool_name,
        trace_id=trace_id,
        branch_id=branch_id,
        attempt=payload.get("attempt", 0),
        duration_ms=payload.get("duration_ms"),
    )
    result = normalize_tool_result(tool_response, meta=meta)
    adapter = runtime.adapters.get(tool_name)
    effect = adapter.to_effect(tool_input, result, epoch)
    runtime.tx_manager.record_effect(tx, effect)

    if result.success:
        runtime.advance_frontier(scopes, epoch)
        try:
            runtime.tx_manager.commit(tx)
        except CommitBlocked:
            store.save_transaction(
                {
                    "tx_id": tx.tx_id,
                    "trace_id": trace_id,
                    "branch_id": branch_id,
                    "epoch": epoch.value,
                    "status": "waiting",
                    "scopes": list(scopes),
                    "reason": None,
                }
            )
            return HookOutcome(status="pending", tx_id=tx.tx_id)
        store.save_transaction(
            {
                "tx_id": tx.tx_id,
                "trace_id": trace_id,
                "branch_id": branch_id,
                "epoch": epoch.value,
                "status": "committed",
                "scopes": list(scopes),
                "reason": None,
            }
        )
        return HookOutcome(status="committed", tx_id=tx.tx_id)

    runtime.tx_manager.abort(tx, result.error or "tool_error")
    store.save_transaction(
        {
            "tx_id": tx.tx_id,
            "trace_id": trace_id,
            "branch_id": branch_id,
            "epoch": epoch.value,
            "status": "aborted",
            "scopes": list(scopes),
            "reason": result.error or "tool_error",
        }
    )
    return HookOutcome(status="aborted", tx_id=tx.tx_id)
